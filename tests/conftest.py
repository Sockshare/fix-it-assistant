"""Shared test fixtures. Every test runs against a throw-away data dir in OFFLINE mode
unless a test explicitly switches providers."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FIXIT_PROVIDER", "offline")
    for k in ("FIXIT_MODEL", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def samples_dir():
    return ROOT / "samples"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from fix_it_assistant.server import app

    # Real browsers send Host: 127.0.0.1; the app rejects anything else (DNS-rebinding guard).
    return TestClient(app, base_url="http://127.0.0.1")
