"""G2 + G6 — API smoke tests and privacy guards. Everything runs offline against a temp data dir."""

from __future__ import annotations

import io
import json
import os
import socket
import struct
import zlib

import pytest

ROOT_API = "/api"


def _png(w=16, h=16):
    """A tiny valid PNG (no Pillow needed)."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_health_ui_status(client):
    assert client.get("/api/health").json()["ok"]
    html = client.get("/").text
    assert "Fix-It Assistant" in html and "/static/app.js" in html
    st = client.get("/api/status").json()
    assert st["provider"]["name"] == "offline" and st["problem_count"] >= 40 and "plumbing" in st["areas"]
    assert st["ollama"].get("skipped") is True  # no probe unless asked


def test_diagnose_basic_flow(client):
    r = client.post("/api/diagnose", data={"description": "My toilet keeps running and hissing after I flush"})
    assert r.status_code == 200
    d = r.json()
    assert d["card"]["id"] == "toilet_running" and d["confident"] and d["flags"] == []
    assert d["card"]["safety"]["level"] == "diy" and d["card"]["steps"] and d["explanation"]
    assert d["engine"] == "rules" and d["photo"]["used"] is False
    rid = d["id"]
    # answers re-rank
    r = client.post("/api/answers", json={"repair_id": rid, "answers": {"pattern": "All the time, never stops"}})
    assert r.json()["card"]["causes"][0]["name"].startswith("Water level")
    # ask (offline rules)
    a = client.post("/api/ask", json={"repair_id": rid, "question": "what tools do I need?"}).json()
    assert "gloves" in a["answer"].lower() and a["engine"] == "rules"
    # notebook
    lst = client.get("/api/repairs").json()
    assert lst[0]["id"] == rid and lst[0]["title"] == "Toilet keeps running"
    full = client.get(f"/api/repairs/{rid}").json()
    assert full["card"]["id"] == "toilet_running" and len(full["chat"]) == 2 and full["answers"]["pattern"]
    assert client.post(f"/api/repairs/{rid}/status", json={"status": "fixed", "note": "new flapper"}).json()["ok"]
    assert client.get("/api/repairs").json()[0]["status"] == "fixed"
    assert client.post(f"/api/repairs/{rid}/status", json={"status": "bogus"}).status_code == 400
    assert client.delete(f"/api/repairs/{rid}").json()["ok"]
    assert client.get(f"/api/repairs/{rid}").status_code == 404


def test_diagnose_emergency_and_no_match(client):
    d = client.post("/api/diagnose", data={"description": "I smell gas near the water heater"}).json()
    assert d["flags"][0]["id"] == "gas_smell" and d["flags"][0]["do"]
    assert client.get("/api/repairs").json()[0]["safety_level"] == "emergency"
    d = client.post("/api/diagnose", data={"description": "my printer is jammed again"}).json()
    assert not d["confident"]
    # unmatched -> ask requires a pick
    d = client.post("/api/diagnose", data={"description": "asdf qwer zxcv uiop"}).json()
    assert d["card"] is None
    assert client.post("/api/ask", json={"repair_id": d["id"], "question": "help"}).status_code == 400
    # picking a problem by hand works
    r = client.post("/api/answers", json={"repair_id": d["id"], "problem_id": "squeaky_hinge", "answers": {}})
    assert r.json()["card"]["title"].startswith("Squeaky")


def test_diagnose_rejects_bad_input(client):
    r = client.post("/api/diagnose", data={"description": "hi"})
    assert r.status_code == 400 and "what is the thing" in r.json()["detail"]
    # area hint must be known; unknown is ignored not fatal
    assert client.post("/api/diagnose", data={"description": "toilet keeps running", "area": "spaceship"}).status_code == 200
    # huge description is bounded
    assert client.post("/api/diagnose", data={"description": "toilet " * 20000}).status_code == 200


def test_photo_validation(client):
    files = {"photo": ("x.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post("/api/diagnose", data={"description": "toilet running"}, files=files)
    assert r.status_code == 400 and "JPG" in r.json()["detail"]
    files = {"photo": ("x.png", io.BytesIO(b"not really a png" * 20), "image/png")}
    r = client.post("/api/diagnose", data={"description": "toilet running"}, files=files)
    assert r.status_code == 400 and "real photo" in r.json()["detail"]
    files = {"photo": ("big.png", io.BytesIO(_png() + b"0" * (9 * 1024 * 1024)), "image/png")}
    r = client.post("/api/diagnose", data={"description": "toilet running"}, files=files)
    assert r.status_code == 400 and "8 MB" in r.json()["detail"]
    # a real png in offline mode: accepted, politely ignored
    files = {"photo": ("ok.png", io.BytesIO(_png()), "image/png")}
    d = client.post("/api/diagnose", data={"description": "toilet keeps running"}, files=files).json()
    assert d["photo"]["used"] and d["photo"]["text"] == "" and "words" in d["photo"]["error"]
    assert d["card"]["id"] == "toilet_running"


def test_browse_and_problem(client):
    b = client.get("/api/browse").json()["areas"]
    assert any(a["area"] == "plumbing" for a in b)
    assert client.get("/api/problem/toilet_running").json()["title"]
    assert client.get("/api/problem/../../etc/passwd").status_code in (404, 422)
    assert client.get("/api/problem/nope").status_code == 404


def test_settings_roundtrip_and_masking(client):
    r = client.post("/api/settings", json={"provider": "gemini", "gemini_api_key": "AIza_dummy_test_key", "setup_complete": True})
    s = r.json()["settings"]
    assert s["gemini_api_key"].startswith("AIz") and "*" in s["gemini_api_key"] and s["has_gemini_key"]
    # masked value posted back leaves the key alone
    client.post("/api/settings", json={"gemini_api_key": s["gemini_api_key"]})
    assert client.get("/api/status").json()["settings"]["has_gemini_key"]
    client.post("/api/settings", json={"gemini_api_key": ""})
    assert not client.get("/api/status").json()["settings"]["has_gemini_key"]
    assert client.post("/api/settings", json={"provider": "nope"}).status_code == 400
    assert client.post("/api/settings", json={"ollama_host": "not a url!!"}).status_code == 400
    r = client.post("/api/settings", json={"provider": "ollama", "vision_model": "llava:7b"})
    assert r.json()["settings"]["vision_model"] == "llava:7b"


def test_config_and_repairs_are_private_files(client, tmp_path):
    client.post("/api/settings", json={"provider": "offline", "setup_complete": True})
    client.post("/api/diagnose", data={"description": "toilet keeps running"})
    data = os.environ["FIXIT_DATA_DIR"]
    for p in [os.path.join(data, "config.json")] + [os.path.join(data, "repairs", f) for f in os.listdir(os.path.join(data, "repairs"))]:
        assert oct(os.stat(p).st_mode & 0o777) == "0o600", p
    assert oct(os.stat(data).st_mode & 0o777) == "0o700"


def test_save_history_off_keeps_repairs_in_memory_only(client):
    client.post("/api/settings", json={"save_history": False})
    d = client.post("/api/diagnose", data={"description": "dryer takes forever to dry"}).json()
    data = os.environ["FIXIT_DATA_DIR"]
    assert not os.path.exists(os.path.join(data, "repairs", d["id"] + ".json"))
    lst = client.get("/api/repairs").json()
    assert any(r["id"] == d["id"] and r.get("unsaved") for r in lst)
    client.post("/api/settings", json={"save_history": True})


def test_delete_all(client):
    client.post("/api/diagnose", data={"description": "toilet keeps running"})
    client.post("/api/diagnose", data={"description": "door squeaks"})
    assert client.delete("/api/repairs").json()["ok"]
    assert client.get("/api/repairs").json() == []


def test_cross_site_writes_are_blocked(client):
    r = client.post("/api/diagnose", data={"description": "toilet keeps running"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.delete("/api/repairs", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/api/diagnose", data={"description": "toilet keeps running"}, headers={"Origin": "http://127.0.0.1:8768"})
    assert r.status_code == 200


def test_bad_ids_are_rejected(client):
    assert client.get("/api/repairs/../config").status_code in (404, 422)
    assert client.get("/api/repairs/zzzzzzzzzzzz").status_code == 404
    assert client.delete("/api/repairs/not-hex").status_code == 404
    assert client.post("/api/answers", json={"repair_id": "x" * 12, "answers": {}}).status_code == 404


def test_no_network_in_offline_mode(client, monkeypatch):
    """G6: with No AI, diagnose/answers/ask/status never open a socket."""
    real_connect = socket.socket.connect

    def guard(self, addr):
        if isinstance(addr, tuple) and addr[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"network call attempted to {addr}")
        return real_connect(self, addr)

    monkeypatch.setattr(socket.socket, "connect", guard)
    # even localhost Ollama must not be probed in offline mode
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("httpx.get called")))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("httpx.post called")))
    st = client.get("/api/status").json()
    assert st["provider"]["name"] == "offline"
    d = client.post("/api/diagnose", data={"description": "my furnace blows cold air"}).json()
    assert d["card"]["id"] == "furnace_no_heat"
    client.post("/api/answers", json={"repair_id": d["id"], "answers": {"what": "Nothing at all"}})
    a = client.post("/api/ask", json={"repair_id": d["id"], "question": "is it safe?"}).json()
    assert a["engine"] == "rules"


def test_status_does_not_contact_cloud_without_probe(client, monkeypatch):
    monkeypatch.setenv("FIXIT_PROVIDER", "gemini")
    client.post("/api/settings", json={"gemini_api_key": "AIza_dummy_test_key"})
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("key would have been sent")))
    st = client.get("/api/status").json()
    assert st["provider"]["ok"] and "Click Test" in st["provider"]["message"]
    client.post("/api/settings", json={"gemini_api_key": ""})


def test_ai_prompts_never_include_other_repairs(client, monkeypatch):
    """The ask prompt is grounded in ONE card + this repair's own description/chat."""
    from fix_it_assistant import ai

    captured = {}

    class Fake:
        name = "fake"

        def complete(self, system, user, want_json=False, images=None):
            captured["user"] = user
            return "Use a 2-inch flapper."

    monkeypatch.setattr(ai, "get_provider", lambda s: Fake())
    monkeypatch.setenv("FIXIT_PROVIDER", "ollama")
    client.post("/api/diagnose", data={"description": "SECRET-OTHER-REPAIR dryer not heating"})
    d = client.post("/api/diagnose", data={"description": "toilet keeps running"}).json()
    client.post("/api/ask", json={"repair_id": d["id"], "question": "which flapper?"})
    assert "SECRET-OTHER-REPAIR" not in captured["user"] and "toilet keeps running" in captured["user"]
