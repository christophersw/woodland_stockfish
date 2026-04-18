"""Leela Chess Zero (Lc0) analysis service.

Lc0 outputs native WDL (Win/Draw/Loss) probabilities in permille (0-1000, sum=1000)
via UCI_ShowWDL=true. This service captures per-move WDL and derives:
  - Win probability for each side at each ply
  - Move quality (win% delta from the mover's perspective)
  - Move classification (brilliant/great/best/excellent/inaccuracy/mistake/blunder)
  - Q-equivalent centipawns via: cp = 111.71 * tan(1.56 * Q)
  - Game-level WDL summary (final position probabilities averaged over the game)

Unlike Stockfish, Lc0 explicitly models draws, making its eval more meaningful for
endgame and fortress positions. The WDL values are from white's perspective, so
the mover's win% for black is the wdl_loss value.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import chess
import chess.engine
import chess.pgn

# Move classification thresholds in win-percentage-loss units (0-100 scale).
# Calibrated to roughly match Lichess/Stockfish thresholds empirically.
_BLUNDER_WP_LOSS = 10.0
_MISTAKE_WP_LOSS = 5.0
_INACCURACY_WP_LOSS = 2.0

# Brilliant/great detection thresholds (win% loss, alternative-win-delta)
_BRILLIANT_MAX_LOSS = 1.0     # move must not drop win% by more than 1%
_BRILLIANT_WIN_CEIL = 70.0    # don't award !! when already clearly winning
_BRILLIANT_ALT_DELTA = 10.0   # 2nd-best must be ≥10% worse for !!
_GREAT_MAX_LOSS = 1.0
_GREAT_ALT_DELTA = 6.0        # alternatives ≥6% worse for !


@dataclass
class Lc0MoveResult:
    ply: int
    san: str
    fen: str
    # WDL permille from white's perspective (sum to 1000)
    wdl_win: int
    wdl_draw: int
    wdl_loss: int
    # Q-derived centipawn equivalent (white positive)
    cp_equiv: float
    best_move: str
    arrow_uci: str
    # Win% drop for the side that just moved (≥ 0, higher = worse move)
    move_win_delta: float
    classification: str


@dataclass
class Lc0PlayerStats:
    # Average final-position WDL from this player's perspective (0-100%)
    avg_win_prob: float
    avg_draw_prob: float
    avg_loss_prob: float
    blunders: int
    mistakes: int
    inaccuracies: int


@dataclass
class Lc0GameResult:
    white_stats: Lc0PlayerStats
    black_stats: Lc0PlayerStats
    moves: list[Lc0MoveResult]
    engine_nodes: int
    network_name: str
    analyzed_at: datetime


def _q_to_cp(q: float) -> float:
    """Convert Lc0 Q value [-1, 1] to centipawn equivalent.

    Formula from lc0 documentation: cp = 111.71 * tan(1.56 * Q)
    Clamped to avoid tan singularity at Q=±1.
    """
    q_clamped = max(-0.9999, min(0.9999, q))
    return 111.71 * math.tan(1.56 * q_clamped)


def _wdl_to_win_pct(wdl_win: int, wdl_draw: int, wdl_loss: int, as_white: bool) -> float:
    """Return mover's win% (0-100) from white-perspective WDL permille values."""
    if as_white:
        return wdl_win / 10.0
    else:
        return wdl_loss / 10.0


def _extract_wdl(info: chess.engine.InfoDict) -> tuple[int, int, int]:
    """Extract WDL permille from engine info. Returns (win, draw, loss) white-perspective."""
    wdl = info.get("wdl")
    if wdl is not None:
        # python-chess returns PovWdl; access via .relative to get Wdl with wins/draws/losses.
        # Reports WDL from the perspective of the side to move; callers invert for white-perspective.
        wdl_rel = wdl.relative if hasattr(wdl, "relative") else wdl
        w, d, l = int(wdl_rel.wins), int(wdl_rel.draws), int(wdl_rel.losses)
        total = w + d + l
        if total > 0 and total != 1000:
            # Normalize to sum to 1000
            w = round(w * 1000 / total)
            d = round(d * 1000 / total)
            l = 1000 - w - d
        return w, d, l
    # Fallback: derive from score Q value
    score = info.get("score")
    if score is not None:
        q_val = score.relative.score(mate_score=10000) or 0
        q_norm = max(-9999, min(9999, q_val)) / 10000.0
        win_p = int(500 + 500 * q_norm)
        draw_p = max(0, 1000 - 2 * abs(win_p - 500))
        loss_p = 1000 - win_p - draw_p
        return win_p, draw_p, loss_p
    return 500, 0, 500


def _classify(
    win_delta: float,
    mover_win_pct_before: float,
    alt_win_delta: float | None,
    is_capture: bool,
) -> str:
    if win_delta >= _BLUNDER_WP_LOSS:
        return "blunder"
    if win_delta >= _MISTAKE_WP_LOSS:
        return "mistake"
    if win_delta >= _INACCURACY_WP_LOSS:
        return "inaccuracy"

    if (
        win_delta <= _BRILLIANT_MAX_LOSS
        and is_capture
        and mover_win_pct_before < _BRILLIANT_WIN_CEIL
        and alt_win_delta is not None
        and alt_win_delta >= _BRILLIANT_ALT_DELTA
    ):
        return "brilliant"

    if (
        win_delta <= _GREAT_MAX_LOSS
        and alt_win_delta is not None
        and alt_win_delta >= _GREAT_ALT_DELTA
    ):
        return "great"

    if win_delta <= _BRILLIANT_MAX_LOSS:
        return "best"
    return "excellent"


def analyze_pgn(
    pgn_text: str,
    lc0_path: str,
    nodes: int = 800,
    move_callback: "callable[[int, int, str], None] | None" = None,
) -> Lc0GameResult:
    """Analyze a full PGN with Lc0 and return per-move WDL results.

    Args:
        pgn_text: Full PGN string.
        lc0_path: Path to the lc0 binary.
        nodes: MCTS node budget per move (higher = stronger but slower).
        move_callback: Optional callback(ply, total, san) called after each move.
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("Could not parse PGN")

    total_moves = sum(1 for _ in game.mainline_moves())
    limit = chess.engine.Limit(nodes=nodes)

    move_results: list[Lc0MoveResult] = []
    white_win_probs: list[float] = []
    white_draw_probs: list[float] = []
    white_loss_probs: list[float] = []
    black_win_probs: list[float] = []
    black_draw_probs: list[float] = []
    black_loss_probs: list[float] = []
    white_deltas: list[float] = []
    black_deltas: list[float] = []

    with chess.engine.SimpleEngine.popen_uci(lc0_path) as engine:
        engine.configure({"UCI_ShowWDL": "true"})

        board = game.board()
        for node in game.mainline():
            move = node.move
            ply = board.ply() + 1
            is_white_move = board.turn == chess.WHITE

            if board.is_game_over() or not any(True for _ in board.legal_moves):
                board.push(move)
                continue

            san = board.san(move)
            is_capture = board.is_capture(move)

            # Analyse position before the move (multipv=2 for brilliant/great detection)
            pre_results = engine.analyse(board, limit, multipv=2)
            if isinstance(pre_results, list):
                pre_top = pre_results[0]
                pre_alt = pre_results[1] if len(pre_results) > 1 else None
            else:
                pre_top = pre_results
                pre_alt = None

            # WDL before move, from the perspective of the side to move
            pre_w, pre_d, pre_l = _extract_wdl(pre_top)
            # pre_w is mover's wins (since python-chess gives relative to side-to-move)
            mover_win_before = pre_w / 10.0

            best_move_obj = pre_top.get("pv", [None])[0]
            best_move_str = best_move_obj.uci() if best_move_obj else ""

            # Win% of the best alternative (to detect only-good-moves)
            alt_win_delta: float | None = None
            if pre_alt is not None:
                alt_w, _, _ = _extract_wdl(pre_alt)
                alt_win_pct = alt_w / 10.0
                alt_win_delta = mover_win_before - alt_win_pct  # how much worse alt is

            # Play the move, analyse after
            board.push(move)
            if board.is_game_over():
                # Terminal position (checkmate/stalemate) — synthesize WDL from outcome.
                # board.turn is now the side that was just mated/stalemated.
                outcome = board.outcome()
                if outcome is not None and outcome.winner is not None:
                    # Checkmate: current turn (loser) has 0 wins, mover won = loss=1000
                    post_w, post_d, post_l = 0, 0, 1000
                else:
                    # Draw (stalemate, etc.)
                    post_w, post_d, post_l = 0, 1000, 0
                post_score = None
            else:
                post_info = engine.analyse(board, limit)
                post_w, post_d, post_l = _extract_wdl(post_info)
                post_score = post_info.get("score")

            # WDL after move — now from the opponent's perspective (board.turn flipped)
            # After the move, it's the opponent's turn. The opponent's WDL win = post_w.
            # The mover's resulting win% = opponent's loss% = post_l / 10.0
            mover_win_after = post_l / 10.0

            win_delta = max(0.0, mover_win_before - mover_win_after)

            classification = _classify(
                win_delta=win_delta,
                mover_win_pct_before=mover_win_before,
                alt_win_delta=alt_win_delta,
                is_capture=is_capture,
            )

            # Q from score for cp_equiv (use post-move score from white's perspective)
            if post_score is not None:
                white_score = post_score.white()
                raw = white_score.score(mate_score=10000) or 0
                cp_eq = _q_to_cp(max(-9999, min(9999, raw)) / 10000.0)
            else:
                # Approximate from WDL: Q ≈ (win - loss) / 1000
                q_approx = (post_w - post_l) / 1000.0
                if not is_white_move:
                    q_approx = -q_approx
                cp_eq = _q_to_cp(q_approx)

            # Convert WDL to white-perspective for storage
            if is_white_move:
                # pre was white-to-move: wdl is already white-perspective
                stored_win = pre_w
                stored_draw = pre_d
                stored_loss = pre_l
            else:
                # pre was black-to-move: invert for white-perspective
                stored_win = pre_l
                stored_draw = pre_d
                stored_loss = pre_w

            # Post-move WDL from white's perspective for stats
            # After move, opponent (other side) is to move. post_w is opponent's wins.
            if is_white_move:
                # White just moved; post is black-to-move. post_w=black wins, post_l=white wins
                w_win_after = post_l / 10.0
                w_draw_after = post_d / 10.0
                w_loss_after = post_w / 10.0
                white_win_probs.append(w_win_after)
                white_draw_probs.append(w_draw_after)
                white_loss_probs.append(w_loss_after)
                white_deltas.append(win_delta)
            else:
                # Black just moved; post is white-to-move. post_w=white wins
                b_win_after = post_l / 10.0
                b_draw_after = post_d / 10.0
                b_loss_after = post_w / 10.0
                black_win_probs.append(b_win_after)
                black_draw_probs.append(b_draw_after)
                black_loss_probs.append(b_loss_after)
                black_deltas.append(win_delta)

            move_results.append(Lc0MoveResult(
                ply=ply,
                san=san,
                fen=board.fen(),
                wdl_win=stored_win,
                wdl_draw=stored_draw,
                wdl_loss=stored_loss,
                cp_equiv=cp_eq,
                best_move=best_move_str,
                arrow_uci=best_move_str,
                move_win_delta=win_delta,
                classification=classification,
            ))

            if move_callback:
                move_callback(ply, total_moves, san)

    def _player_stats(
        win_probs: list[float],
        draw_probs: list[float],
        loss_probs: list[float],
        deltas: list[float],
    ) -> Lc0PlayerStats:
        if not deltas:
            return Lc0PlayerStats(
                avg_win_prob=50.0, avg_draw_prob=0.0, avg_loss_prob=50.0,
                blunders=0, mistakes=0, inaccuracies=0,
            )
        return Lc0PlayerStats(
            avg_win_prob=sum(win_probs) / len(win_probs),
            avg_draw_prob=sum(draw_probs) / len(draw_probs),
            avg_loss_prob=sum(loss_probs) / len(loss_probs),
            blunders=sum(1 for d in deltas if d >= _BLUNDER_WP_LOSS),
            mistakes=sum(1 for d in deltas if _MISTAKE_WP_LOSS <= d < _BLUNDER_WP_LOSS),
            inaccuracies=sum(1 for d in deltas if _INACCURACY_WP_LOSS <= d < _MISTAKE_WP_LOSS),
        )

    # Try to read network name from engine id
    network_name = ""
    try:
        with chess.engine.SimpleEngine.popen_uci(lc0_path) as eng:
            network_name = eng.id.get("name", "")
    except Exception:
        pass

    return Lc0GameResult(
        white_stats=_player_stats(white_win_probs, white_draw_probs, white_loss_probs, white_deltas),
        black_stats=_player_stats(black_win_probs, black_draw_probs, black_loss_probs, black_deltas),
        moves=move_results,
        engine_nodes=nodes,
        network_name=network_name,
        analyzed_at=datetime.now(timezone.utc),
    )
