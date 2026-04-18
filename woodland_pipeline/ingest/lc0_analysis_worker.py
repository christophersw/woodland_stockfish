"""Worker that claims lc0 AnalysisJob rows and runs Leela Chess Zero WDL analysis."""
from __future__ import annotations

import logging
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, and_, func

_IS_TTY = sys.stdout.isatty()

if _IS_TTY:
    from tqdm import tqdm
else:
    def tqdm(iterable=None, **_):  # type: ignore[misc]
        return iterable

from woodland_pipeline.services.lc0_service import analyze_pgn
from woodland_pipeline.storage.database import ENGINE, get_session, init_db
from woodland_pipeline.storage.models import (
    AnalysisJob, Game,
    Lc0GameAnalysis, Lc0MoveAnalysis,
    WorkerHeartbeat,
)

log = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}-lc0"


@dataclass
class _ClaimedJob:
    id: int
    game_id: str
    nodes: int


def _claim_job(nodes: int) -> _ClaimedJob | None:
    is_pg = ENGINE.dialect.name == "postgresql"
    stmt = (
        select(AnalysisJob)
        .where(
            and_(
                AnalysisJob.status == "pending",
                AnalysisJob.engine == "lc0",
            )
        )
        .order_by(AnalysisJob.priority.desc(), AnalysisJob.created_at)
        .limit(1)
    )
    if is_pg:
        stmt = stmt.with_for_update(skip_locked=True)

    with get_session() as session:
        job = session.execute(stmt).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.worker_id = _WORKER_ID
        session.commit()
        return _ClaimedJob(id=job.id, game_id=job.game_id, nodes=job.depth)


def _load_pgn(game_id: str) -> str:
    with get_session() as session:
        game = session.get(Game, game_id)
        return game.pgn if game and game.pgn else ""


def _save_analysis(job: _ClaimedJob, result) -> None:
    with get_session() as session:
        lga = session.execute(
            select(Lc0GameAnalysis).where(Lc0GameAnalysis.game_id == job.game_id)
        ).scalar_one_or_none()

        if lga is None:
            lga = Lc0GameAnalysis(game_id=job.game_id)
            session.add(lga)
            session.flush()

        lga.analyzed_at = result.analyzed_at
        lga.engine_nodes = result.engine_nodes
        lga.network_name = result.network_name
        lga.white_win_prob = result.white_stats.avg_win_prob
        lga.white_draw_prob = result.white_stats.avg_draw_prob
        lga.white_loss_prob = result.white_stats.avg_loss_prob
        lga.black_win_prob = result.black_stats.avg_win_prob
        lga.black_draw_prob = result.black_stats.avg_draw_prob
        lga.black_loss_prob = result.black_stats.avg_loss_prob
        lga.white_blunders = result.white_stats.blunders
        lga.white_mistakes = result.white_stats.mistakes
        lga.white_inaccuracies = result.white_stats.inaccuracies
        lga.black_blunders = result.black_stats.blunders
        lga.black_mistakes = result.black_stats.mistakes
        lga.black_inaccuracies = result.black_stats.inaccuracies

        for old in list(lga.moves):
            session.delete(old)
        session.flush()

        for mr in result.moves:
            session.add(Lc0MoveAnalysis(
                analysis_id=lga.id,
                ply=mr.ply,
                san=mr.san,
                fen=mr.fen,
                wdl_win=mr.wdl_win,
                wdl_draw=mr.wdl_draw,
                wdl_loss=mr.wdl_loss,
                cp_equiv=mr.cp_equiv,
                best_move=mr.best_move,
                arrow_uci=mr.arrow_uci,
                move_win_delta=mr.move_win_delta,
                classification=mr.classification,
            ))

        session.commit()


def _mark_completed(job_id: int) -> None:
    with get_session() as session:
        job = session.get(AnalysisJob, job_id)
        if job:
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            session.commit()


def _mark_failed(job_id: int, error: str) -> None:
    with get_session() as session:
        job = session.get(AnalysisJob, job_id)
        if job:
            job.status = "failed"
            job.error_message = error
            job.retry_count = (job.retry_count or 0) + 1
            session.commit()


def _heartbeat(status: str, current_game_id: str | None = None,
               jobs_completed: int = 0, jobs_failed: int = 0) -> None:
    try:
        with get_session() as session:
            row = session.get(WorkerHeartbeat, _WORKER_ID)
            if row is None:
                row = WorkerHeartbeat(worker_id=_WORKER_ID, started_at=datetime.now(timezone.utc))
                session.add(row)
            row.last_seen = datetime.now(timezone.utc)
            row.status = status
            row.current_game_id = current_game_id
            row.jobs_completed = jobs_completed
            row.jobs_failed = jobs_failed
            session.commit()
    except Exception:
        log.warning("Failed to write heartbeat", exc_info=True)


_STALE_MINUTES = 15


def _recover_stale_jobs() -> int:
    from sqlalchemy import text as sa_text
    is_pg = ENGINE.dialect.name == "postgresql"
    with get_session() as session:
        if is_pg:
            result = session.execute(sa_text(
                "UPDATE analysis_jobs SET status='pending', worker_id=NULL, started_at=NULL "
                f"WHERE engine='lc0' AND status='running' "
                f"AND started_at < NOW() - INTERVAL '{_STALE_MINUTES} minutes'"
            ))
        else:
            result = session.execute(sa_text(
                "UPDATE analysis_jobs SET status='pending', worker_id=NULL, started_at=NULL "
                f"WHERE engine='lc0' AND status='running' "
                f"AND started_at < datetime('now', '-{_STALE_MINUTES} minutes')"
            ))
        session.commit()
        return result.rowcount


def run_worker(
    lc0_path: str,
    nodes: int = 800,
    poll_interval: float = 5.0,
    limit: int | None = None,
) -> None:
    """Main Lc0 worker loop. Claims lc0-engine analysis jobs and runs WDL analysis."""
    init_db()
    recovered = _recover_stale_jobs()
    if recovered:
        log.info("Recovered %d stale lc0 job(s) back to pending.", recovered)
    log.info("Lc0 worker starting. lc0=%s nodes=%d limit=%s", lc0_path, nodes, limit or "∞")

    with get_session() as session:
        total = session.execute(
            select(func.count()).where(
                and_(AnalysisJob.status == "pending", AnalysisJob.engine == "lc0")
            )
        ).scalar_one()
    if limit is not None:
        total = min(total, limit)

    processed = 0
    failed = 0
    _heartbeat("starting", jobs_completed=0, jobs_failed=0)

    try:
        while True:
            if limit is not None and processed >= limit:
                log.info("Reached limit of %d games — exiting.", limit)
                break

            job = _claim_job(nodes)

            if job is None:
                _heartbeat("idle", jobs_completed=processed, jobs_failed=failed)
                if poll_interval <= 0:
                    break
                time.sleep(poll_interval)
                continue

            _heartbeat("analyzing", current_game_id=job.game_id,
                       jobs_completed=processed, jobs_failed=failed)

            try:
                pgn_text = _load_pgn(job.game_id)
                if not pgn_text:
                    raise ValueError("No PGN for game")

                move_bar = tqdm(
                    total=None,
                    desc=f"  {job.game_id[:12]}",
                    unit="ply",
                    leave=False,
                    dynamic_ncols=True,
                ) if _IS_TTY else None

                def on_move(ply: int, total_m: int, san: str) -> None:
                    if move_bar is not None:
                        if move_bar.total is None:
                            move_bar.total = total_m
                        move_bar.set_postfix_str(san, refresh=False)
                        move_bar.update(1)
                    else:
                        if ply % 10 == 0:
                            log.debug("  Lc0 ply %d/%d %s", ply, total_m, san)

                try:
                    result = analyze_pgn(pgn_text, lc0_path=lc0_path, nodes=nodes,
                                         move_callback=on_move)
                finally:
                    if move_bar is not None:
                        move_bar.close()
                _save_analysis(job, result)
                _mark_completed(job.id)
                processed += 1
                log.info(
                    "Lc0 completed job %d (%d/%s)  game=%s  W-win=%.1f%%  B-win=%.1f%%",
                    job.id, processed, limit or "∞", job.game_id,
                    result.white_stats.avg_win_prob, result.black_stats.avg_win_prob,
                )
            except Exception as exc:
                failed += 1
                log.exception("Lc0 job %d FAILED (game=%s): %s", job.id, job.game_id, exc)
                _mark_failed(job.id, str(exc))
                _heartbeat("error", current_game_id=job.game_id,
                           jobs_completed=processed, jobs_failed=failed)
    finally:
        _heartbeat("stopped", jobs_completed=processed, jobs_failed=failed)

    log.info("Lc0 worker done. Processed %d game(s), %d failed.", processed, failed)
