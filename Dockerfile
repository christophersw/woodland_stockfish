FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/games:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish lc0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY woodland_pipeline ./woodland_pipeline

RUN pip install --no-cache-dir .

CMD ["python", "-m", "woodland_pipeline.ingest.run_analysis_worker"]
