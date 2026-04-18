"""CLI entry point for the Lc0 analysis worker."""
from __future__ import annotations

import argparse
import logging

from woodland_pipeline.config import get_settings
from woodland_pipeline.ingest.lc0_analysis_worker import run_worker
from woodland_pipeline.storage.database import get_session, init_db
from woodland_pipeline.storage.models import AnalysisJob, Game

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _enqueue_all(nodes: int) -> None:
    """Enqueue all games that don't yet have an lc0 job."""
    from sqlalchemy import select
    init_db()
    with get_session() as session:
        game_ids = [r[0] for r in session.execute(select(Game.id)).all()]
        existing = {
            r[0] for r in session.execute(
                select(AnalysisJob.game_id).where(AnalysisJob.engine == "lc0")
            ).all()
        }
        new_jobs = [
            AnalysisJob(game_id=gid, engine="lc0", depth=nodes, status="pending")
            for gid in game_ids if gid not in existing
        ]
        session.add_all(new_jobs)
        session.commit()
        log.info("Enqueued %d new lc0 jobs (%d already existed).",
                 len(new_jobs), len(existing))


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Lc0 WDL analysis worker")
    parser.add_argument("--lc0-path", default=settings.lc0_path)
    parser.add_argument("--nodes", type=int, default=settings.lc0_nodes)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--enqueue", action="store_true",
                        help="Enqueue all un-analyzed games before running worker")
    args = parser.parse_args()

    if not args.lc0_path:
        parser.error("LC0_PATH env var or --lc0-path required")

    if args.enqueue:
        _enqueue_all(args.nodes)

    run_worker(
        lc0_path=args.lc0_path,
        nodes=args.nodes,
        poll_interval=args.poll_interval,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
