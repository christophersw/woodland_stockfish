"""
job_submitter.py — Submits pending AnalysisJob rows to the RunPod Serverless endpoint.

Replaces the local Stockfish worker loop (run_analysis_worker.py) when
RUNPOD_ENDPOINT_ID is set.  The RunPod worker writes results directly to
PostgreSQL, so this process is fire-and-forget: it submits jobs and moves on.

Environment variables (all required unless noted):
    RUNPOD_ENDPOINT_ID  — Endpoint ID from the RunPod dashboard
    RUNPOD_API_KEY      — API key from the RunPod dashboard
    DATABASE_URL        — PostgreSQL connection string
    ANALYSIS_DEPTH      — (optional) Stockfish search depth forwarded to worker (default: 20)
    ANALYSIS_THREADS    — (optional) Threads forwarded to worker (default: 8)
    ANALYSIS_HASH_MB    — (optional) Hash MB forwarded to worker (default: 2048)
    SF_POLL_INTERVAL    — (optional) Seconds between submission sweeps (default: 60)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import runpod
from sqlalchemy import and_, select

from stockfish_pipeline.storage.database import get_session, init_db
from stockfish_pipeline.storage.models import AnalysisJob, Game

log = logging.getLogger(__name__)

# Lazy initialization on first use (so module can be imported without RUNPOD_* env vars set)
_INITIALIZED = False
_endpoint = None


def _ensure_initialized() -> None:
    """Initialize RunPod client on first use, reading env vars at that time."""
    global _INITIALIZED, _endpoint
    if _INITIALIZED:
        return

    try:
        runpod_endpoint_id = os.environ["RUNPOD_ENDPOINT_ID"]
        runpod_api_key = os.environ["RUNPOD_API_KEY"]
    except KeyError as e:
        raise RuntimeError(
            f"Missing required env var: {e}. "
            "Set RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY to use the job submitter."
        ) from e

    runpod.api_key = runpod_api_key
    _endpoint = runpod.Endpoint(runpod_endpoint_id)
    _INITIALIZED = True


def _load_pgn(game_id: str) -> str:
    """Load PGN for a game_id in a short-lived session."""
    with get_session() as session:
        game = session.get(Game, game_id)
        return game.pgn if game and game.pgn else ""


def submit_pending_jobs(limit: int | None = None) -> int:
    """
    Query for pending AnalysisJob rows and submit each to RunPod.

    Updates status to "submitted" and stores the RunPod job ID.
    Returns the number of jobs successfully submitted.
    """
    _ensure_initialized()
    
    analysis_depth = int(os.environ.get("ANALYSIS_DEPTH", "20"))
    analysis_threads = int(os.environ.get("ANALYSIS_THREADS", "8"))
    analysis_hash_mb = int(os.environ.get("ANALYSIS_HASH_MB", "2048"))
    
    stmt = (
        select(AnalysisJob)
        .where(
            and_(
                AnalysisJob.status == "pending",
                AnalysisJob.engine == "stockfish",
            )
        )
        .order_by(AnalysisJob.priority.desc(), AnalysisJob.created_at)
    )
    if limit:
        stmt = stmt.limit(limit)

    submitted = 0
    with get_session() as session:
        jobs = session.execute(stmt).scalars().all()

        for job in jobs:
            pgn = _load_pgn(job.game_id)
            if not pgn:
                log.warning("game_id=%s has no PGN — skipping", job.game_id)
                continue

            try:
                run_request = _endpoint.run(
                    {
                        "game_id": job.game_id,
                        "pgn": pgn,
                        "depth": job.depth,
                        "threads": analysis_threads,
                        "hash_mb": analysis_hash_mb,
                    }
                )
                job.runpod_job_id = run_request.job_id
                job.submitted_at = datetime.now(timezone.utc)
                job.status = "submitted"
                log.info(
                    "Submitted game_id=%s → runpod_job_id=%s",
                    job.game_id,
                    run_request.job_id,
                )
                submitted += 1
            except Exception:
                log.exception("Failed to submit game_id=%s", job.game_id)

        session.commit()

    return submitted


def run_submitter_loop() -> None:
    """Continuously submit new pending AnalysisJob rows to RunPod."""
    _ensure_initialized()
    init_db()
    
    poll_interval = int(os.environ.get("SF_POLL_INTERVAL", "60"))
    runpod_endpoint_id = os.environ["RUNPOD_ENDPOINT_ID"]
    
    log.info(
        "Job submitter started — endpoint=%s, poll_interval=%ds",
        runpod_endpoint_id,
        poll_interval,
    )
    while True:
        try:
            n = submit_pending_jobs()
            log.info("Submitted %d job(s). Sleeping %ds.", n, poll_interval)
        except Exception:
            log.exception("Unexpected error in submission sweep — will retry")
        time.sleep(poll_interval)
