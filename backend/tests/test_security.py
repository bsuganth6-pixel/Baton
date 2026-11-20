"""Security regression tests for CSRF, clearance enforcement, and withheld-count privacy."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings


# --- Withheld-count privacy -----------------------------------------------------------------------------------


def test_show_withheld_count_defaults_false_in_config() -> None:
    """Settings default show_withheld_count to False for security."""
    settings = Settings(_env_file=None)
    assert settings.show_withheld_count is False


def test_env_example_sets_show_withheld_count_false() -> None:
    """The .env.example file (repo root) should have SHOW_WITHHELD_COUNT=false."""
    env_path = Path(__file__).resolve().parents[2] / ".env.example"
    content = env_path.read_text(encoding="utf-8")
    assert "SHOW_WITHHELD_COUNT=false" in content


def test_render_yaml_sets_show_withheld_count_false() -> None:
    """The render.yaml deployment config should explicitly set SHOW_WITHHELD_COUNT=false."""
    render_path = Path(__file__).resolve().parents[2] / "render.yaml"
    content = render_path.read_text(encoding="utf-8")
    assert "key: SHOW_WITHHELD_COUNT" in content
    assert 'value: "false"' in content


def test_config_csrf_header_enforced_in_current_user_dep() -> None:
    """Verify the CSRF enforcement logic is in the current_user dependency source."""
    from app.api.deps import current_user
    import inspect
    source = inspect.getsource(current_user)
    assert "csrf_header_missing" in source
    assert "X-Baton-Client" in source or "CSRF_HEADER" in source or "x-baton-client" in source.lower()


def test_config_show_withheld_count_override_possible() -> None:
    """Verify that show_withheld_count can still be explicitly set to true if needed."""
    settings = Settings(_env_file=None, show_withheld_count=True)
    assert settings.show_withheld_count is True