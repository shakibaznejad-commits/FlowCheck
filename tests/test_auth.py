from fastapi.testclient import TestClient
from sqlalchemy import select

import backend.database as database
from backend.database import User
from backend.main import app


def test_signup_creates_user_and_session_cookie() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/signup",
            json={"email": "  qa@example.com ", "password": "a-strong-password"},
        )

        assert response.status_code == 201
        assert response.json() == {"id": 1, "email": "qa@example.com"}
        assert "password" not in response.json()
        assert response.headers["set-cookie"].startswith("flowcheck_session=")
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=lax" in response.headers["set-cookie"].lower()
        with database.engine.connect() as connection:
            password_hash = connection.execute(select(User.password_hash)).scalar_one()
        assert password_hash.startswith("$argon2id$")
        assert password_hash != "a-strong-password"

        me_response = client.get("/api/auth/me")

    assert me_response.status_code == 200
    assert me_response.json() == {"id": 1, "email": "qa@example.com"}


def test_secure_cookie_mode_adds_secure_without_exposing_session_to_javascript(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FLOWCHECK_COOKIE_SECURE", "true")

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/signup",
            json={"email": "secure@example.com", "password": "a-strong-password"},
        )

    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 201
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_signup_rejects_duplicate_email() -> None:
    with TestClient(app) as client:
        payload = {"email": "qa@example.com", "password": "a-strong-password"}
        assert client.post("/api/auth/signup", json=payload).status_code == 201

        response = client.post("/api/auth/signup", json=payload)

    assert response.status_code == 409


def test_login_and_logout_manage_persistent_session() -> None:
    with TestClient(app) as client:
        credentials = {"email": "qa@example.com", "password": "a-strong-password"}
        assert client.post("/api/auth/signup", json=credentials).status_code == 201
        client.post("/api/auth/logout")

        login_response = client.post("/api/auth/login", json=credentials)
        assert login_response.status_code == 200
        assert login_response.json()["email"] == credentials["email"]
        assert client.get("/api/auth/me").status_code == 200

        logout_response = client.post("/api/auth/logout")
        unauthenticated_response = client.get("/api/auth/me")

    assert logout_response.status_code == 204
    assert unauthenticated_response.status_code == 401


def test_invalid_login_does_not_authenticate() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


def test_signup_validates_email_and_minimum_password_length() -> None:
    with TestClient(app) as client:
        invalid_email = client.post(
            "/api/auth/signup",
            json={"email": "not-an-email", "password": "a-strong-password"},
        )
        short_password = client.post(
            "/api/auth/signup",
            json={"email": "qa@example.com", "password": "short"},
        )

    assert invalid_email.status_code == 422
    assert short_password.status_code == 422


def test_auth_required_for_workspace_and_evidence_but_not_health() -> None:
    with TestClient(app) as client:
        health_response = client.get("/health")
        workspace_response = client.post(
            "/api/test-plan",
            json={"url": "https://example.com", "goal": "Test login validation"},
        )
        evidence_response = client.get(
            "/api/evidence/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/test-01-final.png"
        )

    assert health_response.status_code == 200
    assert workspace_response.status_code == 401
    assert evidence_response.status_code == 401


def test_all_workflow_endpoints_require_authentication() -> None:
    plan = {
        "flow": "login",
        "summary": "Check login form.",
        "tests": [{
            "name": "Login validation",
            "description": "Submit invalid credentials.",
            "steps": [{"action": "navigate", "target": "login page", "value": None}],
            "expected_result": "The form rejects invalid credentials.",
        }],
    }
    execution = {
        "status": "failed",
        "run_id": "a" * 32,
        "evidence": [],
        "tests": [{
            "name": "Login validation",
            "status": "failed",
            "steps": [{
                "action": "navigate",
                "target": "login page",
                "status": "failed",
                "message": "Navigation failed.",
            }],
        }],
    }
    base = {"url": "https://example.com/login", "goal": "Test login validation", "plan": plan}
    requests = [
        ("/api/execute-plan", {"url": base["url"], "plan": plan}),
        ("/api/analyze-results", {"goal": base["goal"], "plan": plan, "execution": execution}),
        ("/api/generate-report", {
            **base,
            "execution": execution,
            "analysis": {
                "status": "failed",
                "summary": "Navigation failed.",
                "root_cause": "The page could not be reached.",
                "evidence": ["Navigation failed."],
                "reproduction_steps": ["Open the login page."],
                "recommendation": "Check the target URL.",
            },
        }),
    ]

    with TestClient(app) as client:
        responses = [client.post(path, json=payload) for path, payload in requests]

    assert [response.status_code for response in responses] == [401, 401, 401]
