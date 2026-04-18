# woodland-chess-pipeline

Standalone ingest and analysis code extracted from Woodland Chess.

This repository contains:
- Chess.com ingest pipeline
- Analysis job queueing and worker execution
- Stockfish centipawn analysis and Lc0 WDL neural-network analysis
- SQLAlchemy models and DB bootstrap/migrations required for ingest/analysis
- Lichess opening-book TSV data for opening labeling

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
- `LC0_PATH` (optional; required to run Lc0 WDL analysis)
- `LC0_NODES` (optional; default `800` MCTS nodes per position)

## Sync games from Chess.com

```bash
python -m woodland_pipeline.ingest.run_sync
```

Options:
- `--usernames alice,bob` to override env usernames

## Queue analysis jobs

```bash
python -m woodland_pipeline.ingest.run_analysis_worker --enqueue-only
```

## Run Stockfish worker

```bash
python -m woodland_pipeline.ingest.run_analysis_worker --no-poll
```

Useful flags:
- `--stockfish /path/to/stockfish`
- `--depth 20`
- `--threads 1`
- `--limit 100`
- `--status`

## Combined enqueue + analyze (Stockfish)

```bash
python -m woodland_pipeline.ingest.run_analysis_worker --enqueue --no-poll
```

---

## Lc0 WDL Analysis

Leela Chess Zero outputs native **Win/Draw/Loss probabilities** via `UCI_ShowWDL=true`. The Lc0 worker shares the same `analysis_jobs` queue table, distinguished by an `engine='lc0'` column.

### Queue Lc0 jobs

```bash
python -m woodland_pipeline.ingest.run_lc0_worker --enqueue --lc0-path /path/to/lc0
```

### Run the Lc0 worker

```bash
python -m woodland_pipeline.ingest.run_lc0_worker --lc0-path /path/to/lc0
```

### Combined enqueue + analyze (Lc0)

```bash
python -m woodland_pipeline.ingest.run_lc0_worker --enqueue --lc0-path /path/to/lc0 --nodes 800
```

**Options:**

| Flag | Description |
|---|---|
| `--lc0-path /path/to/lc0` | Path to the `lc0` binary (or set `LC0_PATH` in `.env`) |
| `--nodes N` | MCTS node budget per position (default `800`) |
| `--enqueue` | Enqueue all games without an `lc0` job before running |
| `--limit N` | Stop after N games |
| `--poll-interval N` | Seconds between queue checks; `0` exits when empty |

### Node budget guidance

| Nodes | ~Time/position | Use case |
|---|---|---|
| 200–400 | 0.1–0.3s | Quick bulk pass |
| 800 | 0.5–1s | Default; good balance |
| 2000–5000 | 2–5s | Deep review |
| 10 000+ | 10s+ | Near engine-strength |

GPU acceleration (Metal, CUDA, OpenCL) significantly speeds up Lc0. CPU-only runs are considerably slower.

---

## Notes

- The pipeline defaults to SQLite (`woodland_chess.db`) when `DATABASE_URL` is not set.
- PostgreSQL is recommended for concurrent workers.
- Queue claiming uses `FOR UPDATE SKIP LOCKED` on PostgreSQL.
- Stockfish and Lc0 workers can run simultaneously — they claim different job rows.

---

## How Analysis Calculations Work

### Stockfish Calculations

All Stockfish formulas match the [Lichess open-source implementation](https://github.com/lichess-org/lila/blob/master/modules/analyse/src/main/AccuracyPercent.scala).

#### Engine evaluation

Each position is evaluated by Stockfish (via python-chess) with `multipv=2` so the top two candidate moves are returned — required for brilliant/great move detection. Scores are in **centipawns** (cp) from White's perspective. Mate scores are encoded as ±10000 minus distance-to-mate.

#### Centipawn Loss (CPL)

$$
\text{CPL} = \max\!\big(0,\;\text{eval}_{\text{before}} - \text{eval}_{\text{after}}\big)
$$

Both evals are from the mover's perspective. CPL is always ≥ 0.

#### Win Percentage

Lichess empirical sigmoid ([source](https://github.com/lichess-org/lila/pull/11148)):

$$
\text{Win\%} = 50 + 50 \times \left(\frac{2}{1 + e^{-0.00368208 \times \text{cp}}} - 1\right)
$$

#### Per-move Accuracy

$$
\text{Accuracy\%} = 103.1668100711649 \times e^{-0.04354415386753951 \times \Delta\text{Win\%}} - 3.166924740191411 + 1
$$

Clamped to [0, 100]. If Win% did not decrease, accuracy = 100%. The `+1` is an uncertainty bonus for finite analysis depth.

#### Game Accuracy

Matches Lichess `AccuracyPercent.scala` exactly — blend of volatility-weighted arithmetic mean and harmonic mean:

$$
\text{Game Accuracy} = \frac{\text{WeightedMean} + \text{HarmonicMean}}{2}
$$

Weights are the standard deviation of Win% in a sliding window (size = `max(2, min(8, moves ÷ 10))`), clamped to [0.5, 12]. Volatile positions receive higher weight.

#### Stockfish Move Classification

| Classification | Symbol | Criteria |
|---|---|---|
| Brilliant | !! | CPL < 10, capture, Win% before < 70%, alternatives ≥ 150 cp worse |
| Great | ! | CPL < 10, all alternatives ≥ 80 cp worse |
| Best | — | CPL < 10, neither brilliant nor great |
| Excellent | — | CPL 10–49 |
| Inaccuracy | ?! | CPL 50–99 |
| Mistake | ? | CPL 100–299 |
| Blunder | ?? | CPL ≥ 300 |

Thresholds match Lichess for blunder/mistake/inaccuracy. Brilliant/great detection uses a Chess.com-inspired heuristic based on material sacrifice and move uniqueness.

---

### Lc0 WDL Calculations

#### WDL output and white-perspective normalisation

Lc0 outputs WDL in permille (sum = 1000) from the **side-to-move's perspective** via `UCI_ShowWDL=true`. The service normalises to **White's perspective** before storage:

- White to move → WDL is already White-perspective, store as-is.
- Black to move → flip wins and losses (`stored_win = engine_loss`, `stored_loss = engine_win`), draw unchanged.

`lc0_move_analysis.wdl_win` always represents White's win probability in permille, regardless of whose turn it was.

#### Q value to centipawn equivalent

Lc0 also provides a Q value ∈ [−1, 1] (mean expected outcome across MCTS playouts). This is converted to centipawns for display:

$$
\text{cp}_{\text{equiv}} = 111.71 \times \tan(1.56 \times Q)
$$

Q is clamped to ±0.9999 to avoid the singularity at ±1.

| Q | cp equivalent | Meaning |
|---|---|---|
| 0.0 | 0 | Equal |
| ±0.1 | ≈ ±11 | Slight advantage |
| ±0.3 | ≈ ±35 | Clear advantage |
| ±0.6 | ≈ ±80 | Large advantage |
| ±0.9 | ≈ ±800 | Near-decisive |

#### Move quality: Win% delta

$$
\Delta\text{Win\%} = \max\!\big(0,\;\text{Win\%}_{\text{mover,before}} - \text{Win\%}_{\text{mover,after}}\big)
$$

- **Before:** query engine on position; mover's win% = `wdl_win / 10.0` (engine-relative, so wdl_win = mover's wins).
- **After:** push move, query again; now it's the opponent's turn. Mover's resulting win% = `opponent_wdl_loss / 10.0`.

#### Lc0 Move Classification

Thresholds are in win-percentage-loss units:

| Classification | Symbol | Δ Win% criterion |
|---|---|---|
| Brilliant | !! | Δ ≤ 1%, capture, mover win% < 70%, alt Δ ≥ 10% worse |
| Great | ! | Δ ≤ 1%, best alternative Δ ≥ 6% worse |
| Best | — | Δ ≤ 1%, neither brilliant nor great |
| Excellent | — | 1% < Δ < 2% |
| Inaccuracy | ?! | 2% ≤ Δ < 5% |
| Mistake | ? | 5% ≤ Δ < 10% |
| Blunder | ?? | Δ ≥ 10% |

#### Why draw probability matters

Stockfish's centipawn score cannot distinguish a dead draw (`wdl 50 900 50`) from a chaotic double-edged position (`wdl 450 100 450`) — both might evaluate near 0 cp. Lc0's explicit draw probability exposes this distinction directly.

## Deploy to Railway (polling analysis processor)

This repo is configured for Railway Config-as-Code + Docker build so Stockfish is available at runtime.

Included deployment files:
- `railway.toml` (builder + start command)
- `Dockerfile` (installs Python deps and Stockfish)
- `.dockerignore`

### 1. Create Railway services

- Create a new Railway project from this GitHub repo.
- Add a **PostgreSQL** service in Railway.
- Keep this worker as a **private service** (no public domain required).

### 2. Set environment variables

Set these on the worker service:
- `DATABASE_URL` = `${{Postgres.DATABASE_URL}}`
- `CHESS_COM_USERNAMES` = `alice,bob` (or your username list)
- `ANALYSIS_DEPTH` = `20` (optional)
- `ANALYSIS_THREADS` = `1` (optional)

Optional overrides:
- `STOCKFISH_PATH=/usr/games/stockfish`
- `CHESS_COM_USER_AGENT=woodland-chess-pipeline/0.1`

### 3. Deploy

Push to `main` (or trigger a manual deploy).

This service starts with:

```bash
python -m woodland_pipeline.ingest.run_analysis_worker
```

That command runs in polling mode by default, continuously checking the queue for new pending jobs.

### 4. Enqueue jobs

This worker only processes queued jobs. Enqueue jobs using one of these patterns:

- one-off local/CLI run:

```bash
python -m woodland_pipeline.ingest.run_analysis_worker --enqueue-only
```

- separate Railway service or cron job that runs enqueue logic.
