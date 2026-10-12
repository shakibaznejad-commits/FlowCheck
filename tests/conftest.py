from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import backend.database as database
import backend.main as main
from backend.auth import get_current_user
from backend.database import User


@pytest.fixture(autouse=True)
def isolated_database(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not request.path.name.endswith("test_auth.py"):
        monkeypatch.setenv("FLOWCHECK_AI_APPROVED_USER_IDS", "1")
    test_engine = create_engine(
        f"sqlite:///{(tmp_path / 'flowcheck-test.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(main, "engine", test_engine)
    database.Base.metadata.create_all(bind=test_engine)

    if not request.path.name.endswith(("test_auth.py", "test_runs.py")):
        with Session(test_engine) as db:
            db.add(User(id=1, email="test@example.com", password_hash="unused", created_at=0))
            db.commit()
        test_user = User(id=1, email="test@example.com", password_hash="unused", created_at=0)
        main.app.dependency_overrides[get_current_user] = lambda: test_user

    yield

    main.app.dependency_overrides.clear()
    test_engine.dispose()
