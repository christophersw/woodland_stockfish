"""Unified worker startup script.

Controls which workers run via WORKER_MODE env var:
  stockfish  — run only the Stockfish analysis worker (default)
  lc0        — run only the Lc0 analysis worker
  both       — run both workers concurrently

All worker flags are read from environment variables if not already set.

Stockfish envs:  STOCKFISH_PATH, ANALYSIS_DEPTH, ANALYSIS_THREADS,
                 SF_ENQUEUE, SF_ENQUEUE_ONLY, SF_ENQUEUE_LIMIT,
                 SF_LIMIT, SF_NO_POLL, SF_POLL_INTERVAL

Lc0 envs:        LC0_PATH, LC0_NODES, LC0_NETWORK,
                 LC0_ENQUEUE, LC0_LIMIT, LC0_POLL_INTERVAL
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading

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


def build_stockfish_cmd() -> list[str]:
    cmd = [sys.executable, "-m", "woodland_pipeline.ingest.run_analysis_worker"]

    stockfish_path = _env("STOCKFISH_PATH")
    if stockfish_path:
        cmd += ["--stockfish", stockfish_path]

    depth = _env("ANALYSIS_DEPTH")
    if depth:
        cmd += ["--depth", depth]

    threads = _env("ANALYSIS_THREADS")
    if threads:
        cmd += ["--threads", threads]

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


def build_lc0_cmd() -> list[str]:
    cmd = [sys.executable, "-m", "woodland_pipeline.ingest.run_lc0_worker"]

    lc0_path = _env("LC0_PATH")
    if lc0_path:
        cmd += ["--lc0-path", lc0_path]

    nodes = _env("LC0_NODES")
    if nodes:
        cmd += ["--nodes", nodes]

    if _flag("LC0_ENQUEUE"):
        cmd.append("--enqueue")

    limit = _env("LC0_LIMIT")
    if limit:
        cmd += ["--limit", limit]

    poll_interval = _env("LC0_POLL_INTERVAL")
    if poll_interval:
        cmd += ["--poll-interval", poll_interval]

    return cmd


def run_process(cmd: list[str], label: str) -> int:
    log.info("[%s] starting: %s", label, " ".join(cmd))
    proc = subprocess.run(cmd)
    rc = proc.returncode
    if rc != 0:
        log.error("[%s] exited with code %d", label, rc)
    else:
        log.info("[%s] finished", label)
    return rc


def run_in_thread(cmd: list[str], label: str, results: dict, key: str) -> threading.Thread:
    def _target():
        results[key] = run_process(cmd, label)

    t = threading.Thread(target=_target, name=label, daemon=False)
    t.start()
    return t


def main() -> None:
    mode = _env("WORKER_MODE", "stockfish").lower()
    valid_modes = ("stockfish", "lc0", "both")
    if mode not in valid_modes:
        log.error("WORKER_MODE must be one of %s, got %r", valid_modes, mode)
        sys.exit(1)

    log.info("WORKER_MODE=%s", mode)

    if mode == "stockfish":
        rc = run_process(build_stockfish_cmd(), "stockfish")
        sys.exit(rc)

    if mode == "lc0":
        rc = run_process(build_lc0_cmd(), "lc0")
        sys.exit(rc)

    # both
    results: dict[str, int] = {}
    threads = [
        run_in_thread(build_stockfish_cmd(), "stockfish", results, "stockfish"),
        run_in_thread(build_lc0_cmd(), "lc0", results, "lc0"),
    ]
    for t in threads:
        t.join()

    overall = max(results.values()) if results else 1
    sys.exit(overall)


if __name__ == "__main__":
    main()
