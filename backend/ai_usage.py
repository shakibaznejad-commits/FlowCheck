"""Persistent atomic accounting for Provider calls and browser executions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import secrets
import threading
import time
from typing import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.engine import Connection

from .database import (
    AIExecutionDaily,
    AIExecutionLease,
    AIProviderLease,
    AIUsageDaily,
)


class UsageLimitExceeded(Exception):
    """Raised when daily quota or concurrency capacity is unavailable."""

    def __init__(self, resource: str, message: str) -> None:
        super().__init__(message)
        self.resource = resource


class LeaseLost(Exception):
    """Raised when a lease cannot be renewed or transitioned safely."""


@dataclass
class UsageLimits:
    provider_daily: int = 30
    provider_per_user: int = 1
    provider_global: int = 2
    execution_daily: int = 10
    execution_per_user: int = 1
    execution_global: int = 2
    provider_lease_seconds: int = 600
    execution_lease_seconds: int = 600
    renewal_interval_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "UsageLimits":
        defaults = cls()
        values = {
            "provider_daily": ("FLOWCHECK_AI_DAILY_PROVIDER_CALLS", defaults.provider_daily),
            "provider_per_user": ("FLOWCHECK_AI_PROVIDER_CONCURRENCY_PER_USER", defaults.provider_per_user),
            "provider_global": ("FLOWCHECK_AI_PROVIDER_CONCURRENCY_GLOBAL", defaults.provider_global),
            "execution_daily": ("FLOWCHECK_AI_DAILY_EXECUTIONS", defaults.execution_daily),
            "execution_per_user": ("FLOWCHECK_AI_EXECUTION_CONCURRENCY_PER_USER", defaults.execution_per_user),
            "execution_global": ("FLOWCHECK_AI_EXECUTION_CONCURRENCY_GLOBAL", defaults.execution_global),
            "provider_lease_seconds": ("FLOWCHECK_AI_PROVIDER_LEASE_SECONDS", defaults.provider_lease_seconds),
            "execution_lease_seconds": ("FLOWCHECK_AI_EXECUTION_LEASE_SECONDS", defaults.execution_lease_seconds),
        }
        parsed: dict[str, int | float] = {}
        for field, (name, default) in values.items():
            raw = os.getenv(name)
            parsed[field] = default if raw is None else int(raw)
            if parsed[field] <= 0:
                raise ValueError(f"{name} must be a positive integer")
        interval = os.getenv("FLOWCHECK_AI_LEASE_RENEWAL_SECONDS")
        parsed["renewal_interval_seconds"] = 30.0 if interval is None else float(interval)
        if parsed["renewal_interval_seconds"] <= 0:
            raise ValueError("FLOWCHECK_AI_LEASE_RENEWAL_SECONDS must be positive")
        if parsed["provider_lease_seconds"] <= 60 or parsed["execution_lease_seconds"] <= 300:
            raise ValueError("Lease durations must exceed their maximum request duration")
        shortest_lease = min(
            parsed["provider_lease_seconds"],
            parsed["execution_lease_seconds"],
        )
        if parsed["renewal_interval_seconds"] * 3 >= shortest_lease:
            raise ValueError("Lease renewal interval must be less than one third of lease duration")
        return cls(**parsed)


@dataclass(frozen=True)
class UsageSnapshot:
    provider_reserved: int
    provider_active: int
    provider_attempts: int
    provider_responses: int
    execution_reserved: int
    execution_active: int
    execution_started: int
    execution_completed: int


@contextmanager
def _immediate_transaction(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _utc_date(now: float) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()


class _RenewableLease:
    table = None
    lease_seconds = 0

    def __init__(self, ledger: "UsageLedger", user_id: int, lease_token: str) -> None:
        self.ledger = ledger
        self.user_id = user_id
        self.lease_token = lease_token
        self._closed = threading.Event()
        self._lost = threading.Event()
        self._heartbeat = threading.Thread(
            target=self._renew_loop,
            name=f"flowcheck-lease-{lease_token[:8]}",
            daemon=True,
        )
        self._heartbeat.start()

    def _renew_loop(self) -> None:
        interval = self.ledger.limits.renewal_interval_seconds
        while not self._closed.wait(interval):
            try:
                self.renew()
            except Exception:
                self._lost.set()
                return

    def renew(self) -> None:
        if self._lost.is_set():
            raise LeaseLost("The active work lease could not be renewed.")
        expires_at = int(self.ledger.clock()) + self.lease_seconds
        with _immediate_transaction(self.ledger.engine) as connection:
            result = connection.execute(
                update(self.table)
                .where(
                    self.table.lease_token == self.lease_token,
                    self.table.expires_at > int(self.ledger.clock()),
                )
                .values(expires_at=expires_at)
            )
            if result.rowcount != 1:
                raise LeaseLost("The active work lease expired before renewal.")

    def _assert_active(self) -> None:
        if self._lost.is_set():
            raise LeaseLost("The active work lease could not be renewed.")

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if threading.current_thread() is not self._heartbeat:
            self._heartbeat.join(timeout=max(0.1, self.ledger.limits.renewal_interval_seconds + 0.1))
        with _immediate_transaction(self.ledger.engine) as connection:
            connection.execute(delete(self.table).where(self.table.lease_token == self.lease_token))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


class ProviderLease(_RenewableLease):
    table = AIProviderLease

    def __init__(self, ledger: "UsageLedger", user_id: int, lease_token: str) -> None:
        self.lease_seconds = ledger.limits.provider_lease_seconds
        super().__init__(ledger, user_id, lease_token)

    def mark_dispatched(self) -> None:
        self._assert_active()
        now = int(self.ledger.clock())
        with _immediate_transaction(self.ledger.engine) as connection:
            lease = connection.execute(
                select(AIProviderLease.state, AIProviderLease.expires_at, AIProviderLease.utc_date).where(
                    AIProviderLease.lease_token == self.lease_token
                )
            ).one_or_none()
            if lease is None or lease.state != "reserved" or lease.expires_at <= now:
                raise LeaseLost("The Provider attempt reservation is no longer active.")
            connection.execute(
                update(AIUsageDaily)
                .where(AIUsageDaily.user_id == self.user_id, AIUsageDaily.utc_date == lease.utc_date)
                .values(provider_attempts=AIUsageDaily.provider_attempts + 1)
            )
            connection.execute(
                update(AIProviderLease)
                .where(AIProviderLease.lease_token == self.lease_token)
                .values(state="dispatched", expires_at=now + self.lease_seconds)
            )

    def mark_completed_response(self) -> None:
        self._assert_active()
        with _immediate_transaction(self.ledger.engine) as connection:
            lease = connection.execute(
                select(AIProviderLease.state, AIProviderLease.response_recorded, AIProviderLease.utc_date).where(
                    AIProviderLease.lease_token == self.lease_token
                )
            ).one_or_none()
            if lease is None or lease.state != "dispatched":
                raise LeaseLost("The Provider response has no dispatched attempt.")
            if not lease.response_recorded:
                connection.execute(
                    update(AIUsageDaily)
                    .where(AIUsageDaily.user_id == self.user_id, AIUsageDaily.utc_date == lease.utc_date)
                    .values(provider_responses=AIUsageDaily.provider_responses + 1)
                )
                connection.execute(
                    update(AIProviderLease)
                    .where(AIProviderLease.lease_token == self.lease_token)
                    .values(response_recorded=True)
                )


class ExecutionLease(_RenewableLease):
    table = AIExecutionLease

    def __init__(self, ledger: "UsageLedger", user_id: int, lease_token: str) -> None:
        self.lease_seconds = ledger.limits.execution_lease_seconds
        super().__init__(ledger, user_id, lease_token)

    def mark_started(self) -> None:
        self._assert_active()
        now = int(self.ledger.clock())
        with _immediate_transaction(self.ledger.engine) as connection:
            lease = connection.execute(
                select(AIExecutionLease.state, AIExecutionLease.expires_at, AIExecutionLease.utc_date).where(
                    AIExecutionLease.lease_token == self.lease_token
                )
            ).one_or_none()
            if lease is None or lease.state != "reserved" or lease.expires_at <= now:
                raise LeaseLost("The browser execution reservation is no longer active.")
            connection.execute(
                update(AIExecutionDaily)
                .where(AIExecutionDaily.user_id == self.user_id, AIExecutionDaily.utc_date == lease.utc_date)
                .values(executions_started=AIExecutionDaily.executions_started + 1)
            )
            connection.execute(
                update(AIExecutionLease)
                .where(AIExecutionLease.lease_token == self.lease_token)
                .values(state="started", expires_at=now + self.lease_seconds)
            )

    def mark_completed(self) -> None:
        self._assert_active()
        with _immediate_transaction(self.ledger.engine) as connection:
            lease = connection.execute(
                select(AIExecutionLease.state, AIExecutionLease.utc_date).where(
                    AIExecutionLease.lease_token == self.lease_token
                )
            ).one_or_none()
            if lease is None or lease.state != "started":
                raise LeaseLost("The browser execution has no active started lease.")
            connection.execute(
                update(AIExecutionDaily)
                .where(AIExecutionDaily.user_id == self.user_id, AIExecutionDaily.utc_date == lease.utc_date)
                .values(executions_completed=AIExecutionDaily.executions_completed + 1)
            )


class UsageLedger:
    def __init__(
        self,
        engine: Engine,
        limits: UsageLimits | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.engine = engine
        self.limits = limits or UsageLimits.from_env()
        self.clock = clock

    def reserve_provider_attempt(self, user_id: int) -> ProviderLease:
        token = secrets.token_hex(32)
        now = int(self.clock())
        date = _utc_date(now)
        with _immediate_transaction(self.engine) as connection:
            connection.execute(delete(AIProviderLease).where(AIProviderLease.expires_at <= now))
            connection.execute(
                AIUsageDaily.__table__.insert().prefix_with("OR IGNORE").values(
                    user_id=user_id, utc_date=date,
                    provider_attempts=0, provider_responses=0,
                )
            )
            attempts = self._provider_attempts(connection, user_id, date)
            reserved = connection.execute(
                select(func.count()).select_from(AIProviderLease).where(
                    AIProviderLease.user_id == user_id,
                    AIProviderLease.utc_date == date,
                    AIProviderLease.state == "reserved",
                    AIProviderLease.expires_at > now,
                )
            ).scalar_one()
            if attempts + reserved >= self.limits.provider_daily:
                raise UsageLimitExceeded("provider_daily", "Daily AI Provider call quota exhausted.")
            self._check_concurrency(
                connection, AIProviderLease, user_id, now,
                self.limits.provider_per_user, self.limits.provider_global,
            )
            connection.execute(
                AIProviderLease.__table__.insert().values(
                    lease_token=token,
                    user_id=user_id,
                    utc_date=date,
                    state="reserved",
                    expires_at=now + self.limits.provider_lease_seconds,
                    response_recorded=False,
                )
            )
        return ProviderLease(self, user_id, token)

    def reserve_browser_execution(self, user_id: int) -> ExecutionLease:
        token = secrets.token_hex(32)
        now = int(self.clock())
        date = _utc_date(now)
        with _immediate_transaction(self.engine) as connection:
            connection.execute(delete(AIExecutionLease).where(AIExecutionLease.expires_at <= now))
            connection.execute(
                AIExecutionDaily.__table__.insert().prefix_with("OR IGNORE").values(
                    user_id=user_id, utc_date=date,
                    executions_started=0, executions_completed=0,
                )
            )
            started = self._execution_starts(connection, user_id, date)
            reserved = connection.execute(
                select(func.count()).select_from(AIExecutionLease).where(
                    AIExecutionLease.user_id == user_id,
                    AIExecutionLease.utc_date == date,
                    AIExecutionLease.state == "reserved",
                    AIExecutionLease.expires_at > now,
                )
            ).scalar_one()
            if started + reserved >= self.limits.execution_daily:
                raise UsageLimitExceeded("execution_daily", "Daily browser execution quota exhausted.")
            self._check_concurrency(
                connection, AIExecutionLease, user_id, now,
                self.limits.execution_per_user, self.limits.execution_global,
            )
            connection.execute(
                AIExecutionLease.__table__.insert().values(
                    lease_token=token,
                    user_id=user_id,
                    utc_date=date,
                    state="reserved",
                    expires_at=now + self.limits.execution_lease_seconds,
                )
            )
        return ExecutionLease(self, user_id, token)

    @staticmethod
    def _provider_attempts(connection: Connection, user_id: int, date: str) -> int:
        value = connection.execute(
            select(AIUsageDaily.provider_attempts).where(
                AIUsageDaily.user_id == user_id, AIUsageDaily.utc_date == date
            )
        ).scalar_one_or_none()
        return int(value or 0)

    @staticmethod
    def _execution_starts(connection: Connection, user_id: int, date: str) -> int:
        value = connection.execute(
            select(AIExecutionDaily.executions_started).where(
                AIExecutionDaily.user_id == user_id, AIExecutionDaily.utc_date == date
            )
        ).scalar_one_or_none()
        return int(value or 0)

    @staticmethod
    def _check_concurrency(connection, table, user_id, now, per_user, global_limit) -> None:
        active = select(func.count()).select_from(table).where(table.expires_at > now)
        global_active = connection.execute(active).scalar_one()
        user_active = connection.execute(active.where(table.user_id == user_id)).scalar_one()
        if user_active >= per_user or global_active >= global_limit:
            raise UsageLimitExceeded("concurrency", "AI service concurrency capacity is currently full.")

    def snapshot(self, user_id: int, now: float | None = None) -> UsageSnapshot:
        at = int(self.clock() if now is None else now)
        date = _utc_date(at)
        with self.engine.connect() as connection:
            provider = connection.execute(
                select(AIUsageDaily.provider_attempts, AIUsageDaily.provider_responses).where(
                    AIUsageDaily.user_id == user_id, AIUsageDaily.utc_date == date
                )
            ).one_or_none()
            execution = connection.execute(
                select(AIExecutionDaily.executions_started, AIExecutionDaily.executions_completed).where(
                    AIExecutionDaily.user_id == user_id, AIExecutionDaily.utc_date == date
                )
            ).one_or_none()
            provider_leases = connection.execute(
                select(AIProviderLease.state).where(
                    AIProviderLease.user_id == user_id, AIProviderLease.expires_at > at
                )
            ).scalars().all()
            execution_leases = connection.execute(
                select(AIExecutionLease.state).where(
                    AIExecutionLease.user_id == user_id, AIExecutionLease.expires_at > at
                )
            ).scalars().all()
        return UsageSnapshot(
            provider_reserved=provider_leases.count("reserved"),
            provider_active=len(provider_leases),
            provider_attempts=int(provider.provider_attempts if provider else 0),
            provider_responses=int(provider.provider_responses if provider else 0),
            execution_reserved=execution_leases.count("reserved"),
            execution_active=len(execution_leases),
            execution_started=int(execution.executions_started if execution else 0),
            execution_completed=int(execution.executions_completed if execution else 0),
        )
