from sqlalchemy import create_engine, inspect, text

from backend.database import initialize_database


def test_additive_run_migration_preserves_legacy_rows(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("""
            CREATE TABLE test_runs (
                id VARCHAR(32) PRIMARY KEY,
                user_id INTEGER NOT NULL,
                target_url VARCHAR(2048) NOT NULL,
                goal VARCHAR(2000) NOT NULL,
                flow VARCHAR(16) NOT NULL,
                status VARCHAR(16) NOT NULL,
                created_at INTEGER NOT NULL,
                completed_at INTEGER NOT NULL,
                plan_json JSON NOT NULL,
                execution_json JSON NOT NULL,
                analysis_json JSON NOT NULL,
                report_json JSON NOT NULL,
                evidence_run_id VARCHAR(32) NOT NULL
            )
        """))
        connection.execute(text("""
            INSERT INTO test_runs VALUES (
                'legacy-run', 1, 'https://example.com/login', 'old goal',
                'login', 'failed', 10, 11, '{}', '{}', '{}', '{}', 'legacy-run'
            )
        """))

    initialize_database(engine)
    columns = {item["name"] for item in inspect(engine).get_columns("test_runs")}
    assert {"agent_status", "target_domain", "scope", "source_run_id", "pinned"} <= columns
    tables = set(inspect(engine).get_table_names())
    assert {
        "ai_usage_daily",
        "ai_call_leases",
        "ai_execution_daily",
        "ai_execution_leases",
    } <= tables
    with engine.connect() as connection:
        row = connection.execute(text("SELECT id, status, pinned FROM test_runs WHERE id='legacy-run'")).one()
    assert row == ("legacy-run", "failed", 0)
    engine.dispose()
