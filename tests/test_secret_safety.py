from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_provider_probe_uses_environment_and_has_no_embedded_api_key() -> None:
    source = (ROOT / "test.py").read_text(encoding="utf-8")
    assert 'os.getenv("LLM_API_KEY")' in source
    assert not re.search(r"(?i)\bsk-[a-z0-9_-]{16,}", source)
    assert not re.search(r"(?im)^\s*api_key\s*=\s*['\"][^'\"]{16,}['\"]", source)


def test_environment_examples_keep_ai_allowlist_empty_and_secrets_as_placeholders() -> None:
    for name in (".env.example", ".env.production.example"):
        content = (ROOT / name).read_text(encoding="utf-8")
        assert re.search(r"(?m)^FLOWCHECK_AI_APPROVED_USER_IDS=\s*$", content)
        assert "your-provider-api-key" in content or "your-provider-model" in content
        assert not re.search(r"(?i)\bsk-[a-z0-9_-]{16,}", content)


def test_gitignore_excludes_runtime_secrets_databases_and_evidence() -> None:
    content = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.db", "*.sqlite", "artifacts/", "screenshots/", "certbot/"):
        assert pattern in content
