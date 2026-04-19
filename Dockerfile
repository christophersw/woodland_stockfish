FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/games:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish wget \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q https://github.com/LeelaChessZero/lc0/releases/download/v0.32.0/lc0-v0.32.0-linux-cpu-openblas.tar.gz -O /tmp/lc0.tar.gz \
    && tar -xzf /tmp/lc0.tar.gz -C /tmp \
    && cp /tmp/lc0 /usr/local/bin/lc0 \
    && chmod +x /usr/local/bin/lc0 \
    && rm -rf /tmp/lc0.tar.gz /tmp/lc0

COPY pyproject.toml README.md ./
COPY woodland_pipeline ./woodland_pipeline

RUN pip install --no-cache-dir .

CMD ["python", "-m", "woodland_pipeline.ingest.run_analysis_worker"]
