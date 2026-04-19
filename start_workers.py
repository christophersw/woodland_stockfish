"""Worker startup script for the Stockfish analysis pipeline.

All worker flags are read from environment variables.

Envs:  STOCKFISH_PATH, ANALYSIS_DEPTH, ANALYSIS_THREADS, ANALYSIS_HASH_MB,
       SF_ENQUEUE, SF_ENQUEUE_ONLY, SF_ENQUEUE_LIMIT,
       SF_LIMIT, SF_NO_POLL, SF_POLL_INTERVAL
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("start_workers")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _flag(key: str) -> bool:
    return _env(key).lower() in ("1", "true", "yes")


def build_cmd() -> list[str]:
    cmd = [sys.executable, "-m", "stockfish_pipeline.ingest.run_analysis_worker"]

    stockfish_path = _env("STOCKFISH_PATH")
    if stockfish_path:
        cmd += ["--stockfish", stockfish_path]

    depth = _env("ANALYSIS_DEPTH")
    if depth:
        cmd += ["--depth", depth]

    threads = _env("ANALYSIS_THREADS")
    if threads:
        cmd += ["--threads", threads]

    hash_mb = _env("ANALYSIS_HASH_MB")
    if hash_mb:
        cmd += ["--hash", hash_mb]

    if _flag("SF_ENQUEUE_ONLY"):
        cmd.append("--enqueue-only")
    elif _flag("SF_ENQUEUE"):
        cmd.append("--enqueue")

    enqueue_limit = _env("SF_ENQUEUE_LIMIT")
    if enqueue_limit:
        cmd += ["--enqueue-limit", enqueue_limit]

    limit = _env("SF_LIMIT")
    if limit:
        cmd += ["--limit", limit]

    if _flag("SF_NO_POLL"):
        cmd.append("--no-poll")

    poll_interval = _env("SF_POLL_INTERVAL")
    if poll_interval:
        cmd += ["--poll-interval", poll_interval]

    return cmd


def main() -> None:
    cmd = build_cmd()
    log.info("Starting Stockfish worker: %s", " ".join(cmd))
    proc = subprocess.run(cmd)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
