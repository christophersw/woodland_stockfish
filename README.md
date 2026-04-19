# stockfish-pipeline

Stockfish analysis pipeline for Woodland Chess.

This repository contains:
- Chess.com ingest pipeline
- Stockfish centipawn analysis worker
- Analysis job queueing
- SQLAlchemy models and DB bootstrap
- Lichess opening-book TSV data for opening labeling

> Lc0 WDL analysis has been moved to a separate repo: `lc0-pipeline`

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Configure `.env` with at least:
- `CHESS_COM_USERNAMES` (comma-separated)
- `DATABASE_URL` (optional; defaults to local SQLite)
- `STOCKFISH_PATH` (optional; auto-detected if available on PATH)

## Sync games from Chess.com

```bash
python -m stockfish_pipeline.ingest.run_sync
```

## Run the worker

```bash
python start_workers.py
```

Or invoke directly:

```bash
python -m stockfish_pipeline.ingest.run_analysis_worker --enqueue --no-poll
```

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `STOCKFISH_PATH` | Path to Stockfish binary | auto-detect |
| `ANALYSIS_DEPTH` | Analysis depth | `20` |
| `ANALYSIS_THREADS` | Threads per game | `1` |
| `ANALYSIS_HASH_MB` | Stockfish hash table size (MB) | `256` |
| `SF_ENQUEUE` | Enqueue unanalyzed games before starting | — |
| `SF_ENQUEUE_ONLY` | Enqueue jobs and exit | — |
| `SF_ENQUEUE_LIMIT` | Max games to enqueue | — |
| `SF_LIMIT` | Stop after N games | — |
| `SF_NO_POLL` | Exit when queue empty | — |
| `SF_POLL_INTERVAL` | Seconds between queue checks | `5` |

## Deploy to Railway

- `railway.toml` — builder + start command (`python start_workers.py`)
- `Dockerfile` — installs Python deps and Stockfish sf_18 (avx2)

Required Railway env vars:
- `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
- `CHESS_COM_USERNAMES`
- `SF_ENQUEUE=true` (enqueue unanalyzed games on startup)

## How Stockfish calculations work

All formulas match the [Lichess open-source implementation](https://github.com/lichess-org/lila/blob/master/modules/analyse/src/main/AccuracyPercent.scala).

Each position is evaluated at `multipv=2` (top two moves) for brilliant/great detection. Scores are centipawns from White's perspective.

### Move classification

| Classification | Criteria |
|---|---|
| Brilliant !! | CPL < 10, capture, Win% before < 70%, alternatives ≥ 150 cp worse |
| Great ! | CPL < 10, all alternatives ≥ 80 cp worse |
| Best | CPL < 10 |
| Excellent | CPL 10–49 |
| Inaccuracy ?! | CPL 50–99 |
| Mistake ? | CPL 100–299 |
| Blunder ?? | CPL ≥ 300 |
