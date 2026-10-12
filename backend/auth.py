import hashlib
import os
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import LoginSession, User, get_db
from .schemas import AuthCredentials, AuthUser


SESSION_COOKIE = "flowcheck_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7
password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(32))
router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _normalized_email(email: str) -> str:
    return email.strip().lower()


def _password_hash(password: str) -> str:
    return password_hasher.hash(password)


def _cookie_secure() -> bool:
    return os.getenv("FLOWCHECK_COOKIE_SECURE", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def _issue_session(user: User, db: Session, response: Response) -> None:
    now = int(time.time())
    db.query(LoginSession).filter(LoginSession.expires_at <= now).delete()
    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            user_id=user.id,
            expires_at=now + SESSION_TTL_SECONDS,
        )
    )
    db.commit()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def _public_user(user: User) -> AuthUser:
    return AuthUser(id=user.id, email=user.email)


@router.post("/signup", response_model=AuthUser, status_code=status.HTTP_201_CREATED)
def signup(
    credentials: AuthCredentials,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthUser:
    email = _normalized_email(credentials.email)
    user = User(
        email=email,
        password_hash=_password_hash(credentials.password),
        created_at=int(time.time()),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from exc

    db.refresh(user)
    _issue_session(user, db, response)
    return _public_user(user)


@router.post("/login", response_model=AuthUser)
def login(
    credentials: AuthCredentials,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> AuthUser:
    email = _normalized_email(credentials.email)
    user = db.scalar(select(User).where(User.email == email))
    stored_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_matches = password_hasher.verify(credentials.password, stored_hash)
    if user is None or not password_matches:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    _issue_session(user, db, response)
    return _public_user(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        db.query(LoginSession).filter(LoginSession.token_hash == token_hash).delete()
        db.commit()
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    session = db.get(LoginSession, token_hash)
    now = int(time.time())
    if session is None or session.expires_at <= now:
        if session is not None:
            db.delete(session)
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    user = db.get(User, session.user_id)
    if user is None:
        db.delete(session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return user


@router.get("/me", response_model=AuthUser)
def current_user(user: Annotated[User, Depends(get_current_user)]) -> AuthUser:
    return _public_user(user)
