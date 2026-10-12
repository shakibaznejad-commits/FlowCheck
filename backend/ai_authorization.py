"""Private, default-deny authorization for AI-consuming routes."""

import os
from typing import Annotated

from fastapi import Depends, HTTPException, status

from .auth import get_current_user
from .database import User


class AIUserNotApproved(Exception):
    """Raised when an internal AI service is called without an approved principal."""


def approved_user_ids() -> frozenset[int]:
    raw = os.getenv("FLOWCHECK_AI_APPROVED_USER_IDS", "")
    if not raw.strip():
        return frozenset()
    values: set[int] = set()
    try:
        for item in raw.split(","):
            value = item.strip()
            if not value or not value.isdecimal() or int(value) <= 0:
                return frozenset()
            values.add(int(value))
    except (TypeError, ValueError):
        return frozenset()
    return frozenset(values)


def ensure_ai_user_approved(user_id: int) -> None:
    if user_id not in approved_user_ids():
        raise AIUserNotApproved("AI access is not enabled for this account.")


def require_ai_access(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    try:
        ensure_ai_user_approved(user.id)
    except AIUserNotApproved as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI access is not enabled for this account.",
        ) from exc
    return user
