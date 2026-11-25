import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.scraper import endpoints as ep

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": f"sqlite+aiosqlite:///{tmp_path}/test.db",
        "storage_dir": tmp_path / "storage",
        "min_request_interval": 0,
        "retry_backoff_base": 0,
        "max_retries": 1,
        "download_media": False,
        "api_keys": [],
        "ig_session_ids": [],
        "ig_accounts": [],
        "ig_accounts_file": None,
        "proxies": [],
        "proxy_url": None,
        "proxy_sticky_url": None,
        "secret_key": "test-secret",
        "maintenance_interval_seconds": 0,  # tests drive maintenance explicitly
        "account_wait_seconds": 0.2,
        "alert_webhook_url": None,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def ig() -> Iterator[respx.MockRouter]:
    """Mocks every outgoing Instagram/CDN request. Unmocked requests fail loudly."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def app_factory(tmp_path: Path, ig: respx.MockRouter):
    clients: list[TestClient] = []

    def factory(**overrides: Any) -> TestClient:
        client = TestClient(create_app(make_settings(tmp_path, **overrides)))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(app_factory) -> TestClient:
    return app_factory()


def mock_profile(
    ig: respx.MockRouter, username: str = "natgeo", payload: Any = None, status: int = 200
) -> respx.Route:
    return ig.get(ep.WEB_PROFILE_INFO, params={"username": username}).respond(
        status, json=payload if payload is not None else load("web_profile_info.json")
    )


def mock_clips(ig: respx.MockRouter, status: int = 200, payload: Any = None) -> respx.Route:
    return ig.post(ep.CLIPS_USER).respond(
        status, json=payload if payload is not None else load("clips_user.json")
    )
