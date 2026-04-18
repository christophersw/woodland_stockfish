import threading

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from woodland_pipeline.config import get_settings
from woodland_pipeline.storage.models import Base


settings = get_settings()


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


def _engine():
    if settings.database_url:
        return create_engine(_normalize_database_url(settings.database_url), pool_pre_ping=True)
    return create_engine("sqlite+pysqlite:///woodland_chess.db", pool_pre_ping=True)


ENGINE = _engine()
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False)

_db_initialized = False
_db_lock = threading.Lock()


def init_db() -> None:
    """Initialize DB schema and run migrations. Safe to call many times — only runs once per process."""
    global _db_initialized
    if _db_initialized:
        return
    with _db_lock:
        if not _db_initialized:
            Base.metadata.create_all(ENGINE)
            _run_lightweight_migrations()
            _db_initialized = True


def _add_missing_columns(table: str, column_defs: dict[str, str]) -> set[str]:
    """Add any missing columns to a table. Returns the set of columns that were added."""
    inspector = inspect(ENGINE)
    if not inspector.has_table(table):
        return set()
    existing = {col["name"] for col in inspector.get_columns(table)}
    missing = {name: ddl for name, ddl in column_defs.items() if name not in existing}
    if missing:
        with ENGINE.begin() as conn:
            for name, ddl in missing.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
    return set(missing.keys())


def _run_lightweight_migrations() -> None:
    inspector = inspect(ENGINE)

    # games table
    games_added = _add_missing_columns("games", {
        "white_username": "VARCHAR(120)",
        "black_username": "VARCHAR(120)",
        "white_rating": "INTEGER",
        "black_rating": "INTEGER",
        "result_pgn": "VARCHAR(16)",
        "winner_username": "VARCHAR(120)",
        "lichess_opening": "VARCHAR(200)",
        "opening_ply_1": "VARCHAR(32)",
        "opening_ply_2": "VARCHAR(32)",
        "opening_ply_3": "VARCHAR(32)",
        "opening_ply_4": "VARCHAR(32)",
        "opening_ply_5": "VARCHAR(32)",
    })

    if inspector.has_table("games"):
        existing_games = {col["name"] for col in inspector.get_columns("games")}
        if "winner_username" in games_added or "winner_username" in existing_games:
            with ENGINE.begin() as conn:
                conn.execute(text(
                    "UPDATE games SET winner_username = "
                    "CASE WHEN result_pgn = '1-0' THEN white_username "
                    "WHEN result_pgn = '0-1' THEN black_username "
                    "ELSE NULL END "
                    "WHERE winner_username IS NULL AND result_pgn IS NOT NULL"
                ))

    # game_participants table
    _add_missing_columns("game_participants", {
        "mistake_count": "INTEGER",
        "inaccuracy_count": "INTEGER",
        "acpl": "FLOAT",
    })

    # game_analysis table
    _add_missing_columns("game_analysis", {
        "analyzed_at": "TIMESTAMP",
        "engine_depth": "INTEGER",
        "white_accuracy": "FLOAT",
        "black_accuracy": "FLOAT",
        "white_acpl": "FLOAT",
        "black_acpl": "FLOAT",
        "white_blunders": "INTEGER",
        "white_mistakes": "INTEGER",
        "white_inaccuracies": "INTEGER",
        "black_blunders": "INTEGER",
        "black_mistakes": "INTEGER",
        "black_inaccuracies": "INTEGER",
    })

    # move_analysis table
    _add_missing_columns("move_analysis", {
        "cpl": "FLOAT",
        "classification": "VARCHAR(16)",
    })

    # worker_heartbeats — created by create_all; ensure started_at exists for older DBs
    _add_missing_columns("worker_heartbeats", {
        "started_at": "TIMESTAMP",
        "current_game_id": "VARCHAR(64)",
        "jobs_completed": "INTEGER",
        "jobs_failed": "INTEGER",
    })

    # analysis_jobs — add engine discriminator column
    _add_missing_columns("analysis_jobs", {
        "engine": "VARCHAR(16) DEFAULT 'stockfish'",
    })

    # lc0_game_analysis — new table for Leela Chess Zero WDL analysis
    _add_missing_columns("lc0_game_analysis", {
        "analyzed_at": "TIMESTAMP",
        "engine_nodes": "INTEGER",
        "network_name": "VARCHAR(120)",
        "white_win_prob": "FLOAT",
        "white_draw_prob": "FLOAT",
        "white_loss_prob": "FLOAT",
        "black_win_prob": "FLOAT",
        "black_draw_prob": "FLOAT",
        "black_loss_prob": "FLOAT",
        "white_blunders": "INTEGER",
        "white_mistakes": "INTEGER",
        "white_inaccuracies": "INTEGER",
        "black_blunders": "INTEGER",
        "black_mistakes": "INTEGER",
        "black_inaccuracies": "INTEGER",
    })

    # lc0_move_analysis — per-move WDL data
    _add_missing_columns("lc0_move_analysis", {
        "wdl_win": "INTEGER",
        "wdl_draw": "INTEGER",
        "wdl_loss": "INTEGER",
        "cp_equiv": "FLOAT",
        "best_move": "VARCHAR(32)",
        "arrow_uci": "VARCHAR(8)",
        "move_win_delta": "FLOAT",
        "classification": "VARCHAR(16)",
    })



def get_session() -> Session:
    return SessionLocal()
