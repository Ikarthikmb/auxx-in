"""Tests import the app from web/ with a throwaway data folder. Nothing here
talks to YouTube, Navidrome or the radio."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="auxx-test-")
os.environ.update({"DATA_ROOT": _tmp, "MUSIC_ROOT": _tmp, "RADIO_HOST": "127.0.0.1", "RADIO_TELNET": "9",
                   "LIMIT_ADD": "3 per hour", "NAVIDROME_URL": "", "INTERNAL_KEY": "test-key"})
for k in ("AUTH_PROXY_HEADER", "NAVIDROME_USER", "NAVIDROME_PASS", "LLM_KEY", "LLM_URL"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import pytest  # noqa: E402

import app as aux  # noqa: E402
import auth  # noqa: E402
import config  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Every test starts with no users, no saved configuration and fresh rate limits."""
    for f in (auth.USERS_FILE, auth.DATA / "setup.code", config.FILE, *config.FILES.values()):
        f.unlink(missing_ok=True)
    config._saved.clear()
    aux.limiter.reset()
    monkeypatch.setattr(aux, "radio", lambda *a, **k: "")
    monkeypatch.setattr(aux, "radio_on", lambda: False)
    monkeypatch.setattr(aux, "nd_radio_station", lambda: True)
    yield


@pytest.fixture
def client():
    return aux.app.test_client()


def csrf(c):
    return c.get("/api/me").get_json()["csrf"]


def post(c, path, data=None, token=None):
    return c.post(path, json=data or {}, headers={"X-CSRFToken": token if token is not None else csrf(c)})


@pytest.fixture
def admin(client):
    assert post(client, "/api/setup", {"username": "admin", "password": "correct horse",
                                                "code": auth.setup_code()}).status_code == 200
    return client
