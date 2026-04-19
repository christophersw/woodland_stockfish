from __future__ import annotations

import importlib
from pathlib import Path


def configure_test_db(monkeypatch, tmp_path: Path) -> str:
    db_path = tmp_path / "test_pipeline.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    # Ensure modules pick up the current DATABASE_URL for each test.
    import stockfish_pipeline.storage.database as db_module
    import stockfish_pipeline.storage.models as models_module
    import stockfish_pipeline.ingest.enqueue_analysis as enqueue_module

    importlib.reload(models_module)
    importlib.reload(db_module)
    importlib.reload(enqueue_module)

    return database_url
