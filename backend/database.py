import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[int] = mapped_column(Integer)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class AIUsageDaily(Base):
    __tablename__ = "ai_usage_daily"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    utc_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    provider_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_responses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AIProviderLease(Base):
    __tablename__ = "ai_call_leases"

    lease_token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    utc_date: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    response_recorded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AIExecutionDaily(Base):
    __tablename__ = "ai_execution_daily"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    utc_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    executions_started: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    executions_completed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AIExecutionLease(Base):
    __tablename__ = "ai_execution_leases"

    lease_token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    utc_date: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    target_url: Mapped[str] = mapped_column(String(2048))
    goal: Mapped[str] = mapped_column(String(2000))
    flow: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[int] = mapped_column(Integer, index=True)
    completed_at: Mapped[int] = mapped_column(Integer)
    plan_json: Mapped[dict] = mapped_column(JSON)
    execution_json: Mapped[dict] = mapped_column(JSON)
    analysis_json: Mapped[dict] = mapped_column(JSON)
    report_json: Mapped[dict] = mapped_column(JSON)
    evidence_run_id: Mapped[str] = mapped_column(String(32), index=True)
    agent_status: Mapped[str] = mapped_column(String(16), default="completed")
    target_domain: Mapped[str] = mapped_column(String(255), default="")
    scope: Mapped[str] = mapped_column(String(16), default="focused")
    source_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SavedTestSuite(Base):
    __tablename__ = "saved_test_suites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    target_url: Mapped[str] = mapped_column(String(2048))
    goal: Mapped[str] = mapped_column(String(2000))
    plan_json: Mapped[dict] = mapped_column(JSON)
    source_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, index=True)


def initialize_database(target_engine: Engine) -> None:
    """Create current tables and apply safe additive SQLite columns."""
    Base.metadata.create_all(bind=target_engine)
    if target_engine.dialect.name != "sqlite":
        return
    existing = {column["name"] for column in inspect(target_engine).get_columns("test_runs")}
    additions = {
        "agent_status": "VARCHAR(16) NOT NULL DEFAULT 'completed'",
        "target_domain": "VARCHAR(255) NOT NULL DEFAULT ''",
        "scope": "VARCHAR(16) NOT NULL DEFAULT 'focused'",
        "source_run_id": "VARCHAR(32)",
        "pinned": "BOOLEAN NOT NULL DEFAULT 0",
    }
    with target_engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE test_runs ADD COLUMN {name} {sql_type}"))


def _create_engine() -> Engine:
    database_path = Path(
        os.getenv(
            "FLOWCHECK_DATABASE_PATH",
            str(Path(__file__).parent / "data" / "flowcheck.db"),
        )
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )


engine: Engine = _create_engine()


def get_db() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
