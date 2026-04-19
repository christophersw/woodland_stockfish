from __future__ import annotations

from stockfish_pipeline.ingest.sync_service import ChessComSyncService


def test_normalize_result_variants() -> None:
    assert ChessComSyncService._normalize_result("win") == "Win"
    assert ChessComSyncService._normalize_result("resigned") == "Loss"
    assert ChessComSyncService._normalize_result("repetition") == "Draw"
    assert ChessComSyncService._normalize_result("unexpected") == "Draw"


def test_stable_game_id_is_deterministic() -> None:
    payload = {
        "url": "https://www.chess.com/game/live/123",
        "end_time": 1712100000,
        "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6",
    }
    first = ChessComSyncService._stable_game_id(payload)
    second = ChessComSyncService._stable_game_id(payload)
    assert first == second
    assert len(first) == 24
