from concurrent.futures import ThreadPoolExecutor
import time

import pytest
from sqlalchemy import create_engine

from backend.database import (
    AIExecutionDaily,
    AIExecutionLease,
    AIProviderLease,
    AIUsageDaily,
    Base,
    User,
)
from backend.ai_usage import UsageLedger, UsageLimitExceeded, UsageLimits


@pytest.fixture
def ledger(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'usage.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            [
                {"id": 1, "email": "owner@example.test", "password_hash": "unused", "created_at": 1},
                {"id": 2, "email": "guest@example.test", "password_hash": "unused", "created_at": 1},
            ],
        )
    yield UsageLedger(engine, UsageLimits())
    engine.dispose()


def test_provider_reservation_is_released_before_dispatch(ledger):
    lease = ledger.reserve_provider_attempt(1)
    before = ledger.snapshot(1)
    assert before.provider_reserved == 1
    assert before.provider_attempts == 0
    assert before.provider_active == 1

    lease.close()
    after = ledger.snapshot(1)
    assert after.provider_reserved == 0
    assert after.provider_attempts == 0
    assert after.provider_active == 0


def test_provider_attempt_is_not_refunded_after_dispatch_failure(ledger):
    lease = ledger.reserve_provider_attempt(1)
    lease.mark_dispatched()
    lease.close()  # Simulates timeout/cancellation after dispatch.

    snapshot = ledger.snapshot(1)
    assert snapshot.provider_attempts == 1
    assert snapshot.provider_responses == 0
    assert snapshot.provider_active == 0


def test_provider_response_is_recorded_before_result_validation(ledger):
    lease = ledger.reserve_provider_attempt(1)
    lease.mark_dispatched()
    lease.mark_completed_response()
    lease.close()

    snapshot = ledger.snapshot(1)
    assert snapshot.provider_attempts == 1
    assert snapshot.provider_responses == 1


def test_concurrent_provider_reservations_cannot_overrun_user_quota(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'race.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {"id": 1, "email": "owner@example.test", "password_hash": "unused", "created_at": 1},
        )
    usage = UsageLedger(engine, UsageLimits(provider_daily=1, provider_per_user=4, provider_global=4))

    def reserve():
        try:
            return usage.reserve_provider_attempt(1)
        except UsageLimitExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = list(pool.map(lambda _: reserve(), range(8)))
    active = [lease for lease in leases if lease is not None]
    assert len(active) == 1
    for lease in active:
        lease.close()
    engine.dispose()


def test_pre_dispatch_failure_frees_the_last_daily_reservation(ledger):
    ledger.limits.provider_daily = 1
    first = ledger.reserve_provider_attempt(1)
    first.close()
    second = ledger.reserve_provider_attempt(1)
    second.close()


def test_dispatched_attempt_consumes_daily_quota_after_failure(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'quota.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {"id": 1, "email": "owner@example.test", "password_hash": "unused", "created_at": 1},
        )
    usage = UsageLedger(engine, UsageLimits(provider_daily=1))
    first = usage.reserve_provider_attempt(1)
    first.mark_dispatched()
    first.close()
    with pytest.raises(UsageLimitExceeded):
        usage.reserve_provider_attempt(1)
    engine.dispose()


def test_browser_execution_has_a_separate_daily_quota(ledger):
    ledger.limits.execution_daily = 1
    lease = ledger.reserve_browser_execution(1)
    lease.mark_started()
    lease.mark_completed()
    lease.close()

    snapshot = ledger.snapshot(1)
    assert snapshot.execution_started == 1
    assert snapshot.execution_completed == 1
    assert snapshot.provider_attempts == 0
    with pytest.raises(UsageLimitExceeded):
        ledger.reserve_browser_execution(1)


def test_browser_reservation_before_launch_does_not_consume_execution_quota(ledger):
    ledger.limits.execution_daily = 1
    first = ledger.reserve_browser_execution(1)
    first.close()
    second = ledger.reserve_browser_execution(1)
    second.close()
    assert ledger.snapshot(1).execution_started == 0


def test_lease_renewal_interval_must_leave_a_safety_margin(monkeypatch):
    from backend.ai_usage import UsageLimits

    monkeypatch.setenv("FLOWCHECK_AI_PROVIDER_LEASE_SECONDS", "600")
    monkeypatch.setenv("FLOWCHECK_AI_EXECUTION_LEASE_SECONDS", "600")
    monkeypatch.setenv("FLOWCHECK_AI_LEASE_RENEWAL_SECONDS", "300")
    with pytest.raises(ValueError, match="renewal interval"):
        UsageLimits.from_env()


def test_global_provider_concurrency_is_atomic_across_users(ledger):
    ledger.limits.provider_global = 1
    first = ledger.reserve_provider_attempt(1)
    with pytest.raises(UsageLimitExceeded):
        ledger.reserve_provider_attempt(2)
    first.close()
    next_user = ledger.reserve_provider_attempt(2)
    next_user.close()


def test_expired_pre_dispatch_lease_releases_reserved_quota_and_capacity(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'expiry.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {"id": 1, "email": "owner@example.test", "password_hash": "unused", "created_at": 1},
        )
    now = [1_000.0]
    usage = UsageLedger(
        engine,
        UsageLimits(
            provider_daily=1,
            provider_global=1,
            provider_lease_seconds=61,
            renewal_interval_seconds=100,
        ),
        clock=lambda: now[0],
    )
    abandoned = usage.reserve_provider_attempt(1)
    now[0] += 62
    recovered = usage.reserve_provider_attempt(1)
    assert usage.snapshot(1).provider_attempts == 0
    recovered.close()
    abandoned.close()
    engine.dispose()


def test_provider_heartbeat_keeps_an_active_slot_reserved(ledger):
    ledger.limits.provider_global = 1
    ledger.limits.provider_lease_seconds = 61
    ledger.limits.renewal_interval_seconds = 0.05
    active = ledger.reserve_provider_attempt(1)
    active.mark_dispatched()
    time.sleep(0.16)
    assert ledger.snapshot(1).provider_active == 1
    with pytest.raises(UsageLimitExceeded):
        ledger.reserve_provider_attempt(2)
    active.close()
    recovered = ledger.reserve_provider_attempt(2)
    recovered.close()


def test_crashed_dispatched_provider_attempt_expires_without_refund(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'provider-crash.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {"id": 1, "email": "owner@example.test", "password_hash": "unused", "created_at": 1},
        )
    now = [1_000.0]
    usage = UsageLedger(engine, UsageLimits(provider_daily=2), clock=lambda: now[0])
    with engine.begin() as connection:
        connection.execute(AIUsageDaily.__table__.insert().values(
            user_id=1, utc_date="1970-01-01", provider_attempts=1, provider_responses=0,
        ))
        connection.execute(AIProviderLease.__table__.insert().values(
            lease_token="crashed-provider", user_id=1, utc_date="1970-01-01",
            state="dispatched", expires_at=1_000, response_recorded=False,
        ))
    now[0] += 601
    recovered = usage.reserve_provider_attempt(1)
    assert usage.snapshot(1).provider_attempts == 1
    assert usage.snapshot(1).provider_active == 1
    recovered.close()
    engine.dispose()


def test_crashed_execution_lease_frees_capacity_but_keeps_daily_start(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'execution-crash.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            User.__table__.insert(),
            {"id": 1, "email": "owner@example.test", "password_hash": "unused", "created_at": 1},
        )
    now = [1_000.0]
    usage = UsageLedger(engine, UsageLimits(execution_daily=2), clock=lambda: now[0])
    with engine.begin() as connection:
        connection.execute(AIExecutionDaily.__table__.insert().values(
            user_id=1, utc_date="1970-01-01", executions_started=1, executions_completed=0,
        ))
        connection.execute(AIExecutionLease.__table__.insert().values(
            lease_token="crashed-execution", user_id=1, utc_date="1970-01-01",
            state="started", expires_at=1_000,
        ))
    now[0] += 601
    replacement = usage.reserve_browser_execution(1)
    assert usage.snapshot(1).execution_started == 1
    assert usage.snapshot(1).execution_active == 1
    replacement.close()
    engine.dispose()
