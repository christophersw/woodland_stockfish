# stockfish-pipeline

Archived repository.

This repo is no longer a deployment target.

Canonical replacements:
- Railway dispatcher: `woodland_dispatchers`
- Stockfish RunPod worker: `woodland_chess_runpod`

Keep this repo only as historical/source material unless you intentionally migrate additional code out of it.

Stockfish analysis pipeline for Woodland Chess.

This repository contains:
- Chess.com ingest pipeline
- RunPod job submitter for Stockfish analysis
- Analysis job queueing and submission
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
- `DATABASE_URL`
- `RUNPOD_ENDPOINT_ID`
- `RUNPOD_API_KEY`

## Sync games from Chess.com

```bash
python -m stockfish_pipeline.ingest.run_sync
```

## Run the worker

```bash
python start_workers.py
```

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `RUNPOD_ENDPOINT_ID` | RunPod serverless endpoint ID | required |
| `RUNPOD_API_KEY` | RunPod API key | required |
| `DATABASE_URL` | Postgres connection string | required |
| `ANALYSIS_DEPTH` | Analysis depth forwarded to RunPod worker payload | `20` |
| `ANALYSIS_THREADS` | Threads forwarded to RunPod worker payload | `8` |
| `ANALYSIS_HASH_MB` | Hash MB forwarded to RunPod worker payload | `2048` |
| `SF_POLL_INTERVAL` | Seconds between submission sweeps | `60` |

## Deploy to Railway

- `railway.toml` — builder + start command (`python start_workers.py`)
- `Dockerfile` — installs Python deps and runs the RunPod submitter

Required Railway env vars:
- `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
- `RUNPOD_ENDPOINT_ID`
- `RUNPOD_API_KEY`

Optional Railway env vars:
- `ANALYSIS_DEPTH`
- `ANALYSIS_THREADS`
- `ANALYSIS_HASH_MB`
- `SF_POLL_INTERVAL`

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
