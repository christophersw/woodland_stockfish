from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from tests.conftest import configure_test_db


def test_enqueue_unanalyzed_creates_pending_job(monkeypatch, tmp_path) -> None:
    configure_test_db(monkeypatch, tmp_path)

    from stockfish_pipeline.ingest.enqueue_analysis import enqueue_unanalyzed
    from stockfish_pipeline.storage.database import get_session, init_db
    from stockfish_pipeline.storage.models import AnalysisJob, Game, Player

    init_db()

    with get_session() as session:
        player = Player(username="alice", display_name="alice")
        session.add(player)
        session.flush()

        game = Game(
            id="g1",
            played_at=datetime.now(UTC),
            player_id=player.id,
            opponent_name="bob",
            color="White",
            result="Win",
            player_rating=1500,
            opponent_rating=1450,
            time_control="600",
            pgn="1. e4 e5 2. Nf3 Nc6 3. Bb5 a6",
        )
        session.add(game)
        session.commit()

    created = enqueue_unanalyzed(depth=18)
    assert created == 1

    with get_session() as session:
        jobs = session.execute(select(AnalysisJob)).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].game_id == "g1"
        assert jobs[0].status == "pending"
        assert jobs[0].depth == 18


def test_enqueue_skips_when_completed_job_exists(monkeypatch, tmp_path) -> None:
    configure_test_db(monkeypatch, tmp_path)

    from stockfish_pipeline.ingest.enqueue_analysis import enqueue_unanalyzed
    from stockfish_pipeline.storage.database import get_session, init_db
    from stockfish_pipeline.storage.models import AnalysisJob, Game, Player

    init_db()

    with get_session() as session:
        player = Player(username="alice", display_name="alice")
        session.add(player)
        session.flush()

        game = Game(
            id="g2",
            played_at=datetime.now(UTC),
            player_id=player.id,
            opponent_name="bob",
            color="Black",
            result="Loss",
            player_rating=1400,
            opponent_rating=1500,
            time_control="600",
            pgn="1. d4 d5 2. c4 e6",
        )
        session.add(game)
        session.flush()

        session.add(AnalysisJob(game_id="g2", status="completed", depth=20, priority=0))
        session.commit()

    created = enqueue_unanalyzed(depth=18)
    assert created == 0
