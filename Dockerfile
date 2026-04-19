FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install CPU-optimized Stockfish sf_18 (bmi2) when RUNPOD_ENDPOINT_ID is NOT
# set — i.e. local/Railway analysis mode.  The RunPod worker has its own image
# with Stockfish; Railway only needs the job submitter, so we keep Stockfish
# optional by installing it only when explicitly requested via build arg.
ARG INSTALL_STOCKFISH=false
RUN if [ "$INSTALL_STOCKFISH" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends curl tar \
        && rm -rf /var/lib/apt/lists/* \
        && curl -fsSL "https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64-bmi2.tar" \
               -o /tmp/stockfish.tar \
        && tar -xf /tmp/stockfish.tar -C /tmp \
        && find /tmp -name "stockfish*" -type f -perm /111 | head -1 | xargs -I{} mv {} /usr/local/bin/stockfish \
        && chmod +x /usr/local/bin/stockfish \
        && rm -f /tmp/stockfish.tar; \
    fi

ENV STOCKFISH_PATH=/usr/local/bin/stockfish

COPY pyproject.toml README.md ./
COPY stockfish_pipeline ./stockfish_pipeline
COPY start_workers.py ./

RUN pip install --no-cache-dir .

CMD ["python", "start_workers.py"]
