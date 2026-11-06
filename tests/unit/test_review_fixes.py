"""Regression tests verifying all findings from CODE_REVIEW.md and NOVICE_REVIEW.md are resolved."""

from __future__ import annotations

import io
import struct
import tempfile
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from fix_it_assistant import ai, config, knowledge as K, server
from fix_it_assistant.config import Settings
from fix_it_assistant.llm import providers


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FIXIT_PROVIDER", "offline")
    for k in ("FIXIT_MODEL", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "OLLAMA_HOST"):
        monkeypatch.delenv(k, raising=False)
    server._MEM.clear()
    yield
    server._MEM.clear()


# ---------------------------------------------------------------------------
# 1. knowledge.py: RED_FLAGS precision & recall, stemmer, typos, out-of-scope
# ---------------------------------------------------------------------------
def test_co_alarm_no_false_positive_on_low_battery_chirp():
    text = "My carbon monoxide detector chirps once every minute at night, low battery"
    flags = K.red_flags(text)
    assert not any(f["id"] == "co_alarm" for f in flags)
    m = K.match(text)
    assert m["candidates"][0]["id"] == "smoke_alarm_chirp"


@pytest.mark.parametrize("text", [
    "There is a wide crack in the foundation",
    "I see a horizontal crack in the basement wall",
    "Stair-step crack in the brick foundation wall",
    "There is a growing crack in the main beam",
    "The basement wall is bowing inward",
])
def test_structural_red_flag_detected_on_natural_word_order(text):
    flags = K.red_flags(text)
    assert any(f["id"] == "structural" for f in flags)
    m = K.match(text)
    if m["candidates"]:
        c = K.card(m["candidates"][0]["id"], description=text, has_flags=True)
        assert c["safety"]["level"] == "emergency"


@pytest.mark.parametrize("text,expected_flag", [
    ("the ceiling light is sparking and smoking", "electrical_fire"),
    ("my light fixture smells like burning plastic", "electrical_fire"),
    ("scorched outlet in the kitchen", "electrical_fire"),
    ("the ceiling light has water dripping from it", "water_electric"),
    ("my fuse box is wet from a leak", "water_electric"),
    ("hissing sound coming from the gas pipe behind the stove", "gas_smell"),
    ("when the furnace runs I get a headache and feel dizzy", "co_alarm"),
])
def test_red_flags_detect_natural_word_order_and_synonyms(text, expected_flag):
    ids = [f["id"] for f in K.red_flags(text)]
    assert expected_flag in ids


def test_stemmer_handles_silent_e_plurals_and_4_letter_plurals():
    assert K._stem("tires") == K._stem("tire")
    assert K._stem("pipes") == K._stem("pipe")
    assert K._stem("hinges") == K._stem("hinge")
    assert K._stem("wires") == K._stem("wire")
    assert K._stem("cars") == K._stem("car")
    assert K._stem("tubs") == K._stem("tub")

    m = K.match("my tires have a slow leak")
    assert m["candidates"][0]["id"] == "tire_pressure_light"


def test_obd_code_matches_check_engine():
    m = K.match("my car threw P0420")
    assert m["candidates"][0]["id"] == "check_engine"
    assert "p0" in m["candidates"][0]["matched"]


def test_typos_and_out_of_scope_guard():
    assert K.match("tolet keeps runing")["candidates"][0]["id"] == "toilet_running"
    assert K.match("dishwasher wont drian")["candidates"][0]["id"] == "dishwasher_not_draining"
    assert K.match("refrigerater is warm")["candidates"][0]["id"] == "fridge_warm"
    assert K.match("garbage disposel hums")["candidates"][0]["id"] == "disposal_hum"
    assert K.match("toilet handle is floppy and doesn't flush")["candidates"][0]["id"] == "toilet_weak_flush"
    # Out-of-scope electronics should not match home appliances/doors
    assert K.match("My laptop screen is cracked and won't turn on")["candidates"] == []
    # Broken garage spring escalates card safety to pro
    c = K.card("garage_door_wont_close", description="heard a loud bang in the garage and now the door is stuck")
    assert c["safety"]["level"] == "pro"


# ---------------------------------------------------------------------------
# 2. ai.py: malformed JSON fallback, safety guard, template Q&A
# ---------------------------------------------------------------------------
def test_explain_invalid_json_keeps_fallback_pick(monkeypatch):
    m = K.match("My toilet keeps running after I flush")
    assert m["candidates"][0]["id"] == "toilet_running"

    class BrokenJSONProvider:
        name = "ollama"
        def complete(self, system, user, want_json=False, images=None):
            return "Sorry, I think this is toilet_running because the flapper is worn."

    monkeypatch.setattr(ai, "get_provider", lambda s: BrokenJSONProvider())
    res = ai.explain("My toilet keeps running after I flush", "", m["candidates"], Settings(provider="ollama"))
    assert res["pick"] == "toilet_running"


def test_safety_guard_fires_on_hazardous_diy_and_escalated_cards(monkeypatch):
    class UnsafeModel:
        name = "ollama"
        def complete(self, system, user, want_json=False, images=None):
            return "You can easily wind the torsion spring yourself with two screwdrivers, go ahead!"

    monkeypatch.setattr(ai, "get_provider", lambda s: UnsafeModel())
    ans = ai.ask("garage_door_wont_close", "garage door spring broke", "can I fix the spring?", [], Settings(provider="ollama"))
    assert ans["answer"].startswith("⚠️")


def test_template_ask_answers_first_step_parts_and_tool_substitutes():
    s = Settings(provider="offline")
    r1 = ai.ask("toilet_running", "toilet runs", "can I do this myself or what should I try first?", [], s)
    assert r1["answer"].startswith("Start here:")
    r2 = ai.ask("toilet_running", "toilet runs", "which size flapper do I need?", [], s)
    assert "Universal toilet flapper" in r2["answer"]
    r3 = ai.ask("outlet_dead", "dead outlet", "can I use a butter knife instead of a screwdriver?", [], s)
    assert "Avoid using knives" in r3["answer"]


# ---------------------------------------------------------------------------
# 3. config.py & providers.py: validation, corrupt config, error messages
# ---------------------------------------------------------------------------
def test_invalid_openai_base_url_rejected_with_400(monkeypatch):
    monkeypatch.delenv("FIXIT_PROVIDER", raising=False)
    c = TestClient(server.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    r = c.post("/api/settings", json={"provider": "openai", "openai_base_url": "http://127.0.0.1:notaport", "openai_api_key": "sk-test1234567890"})
    assert r.status_code == 400


def test_corrupt_non_dict_config_does_not_crash(monkeypatch, tmp_path):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    (d / "config.json").write_text("null", encoding="utf-8")
    monkeypatch.setenv("FIXIT_DATA_DIR", str(d))
    s = config.load_settings()
    assert isinstance(s, Settings)


def test_ollama_down_with_photo_reports_ollama_not_running(monkeypatch):
    def fail_connect(*a, **k):
        raise httpx.ConnectError("Connection refused")
    monkeypatch.setattr(httpx, "get", fail_connect)
    monkeypatch.setattr(httpx, "post", fail_connect)

    p = providers.OllamaProvider(Settings(provider="ollama"))
    with pytest.raises(providers.ProviderError) as exc:
        p.complete("sys", "user", images=[(b"123", "image/png")])
    assert "ollama serve" in str(exc.value)


def test_ollama_tag_mismatch_detected(monkeypatch):
    monkeypatch.setattr(providers, "detect_ollama", lambda host="": {"running": True, "models": ["llama3.2:1b"]})
    p = providers.OllamaProvider(Settings(provider="ollama", model="llama3.2:3b"))
    ok, msg = p.check()
    assert ok is False and "not downloaded" in msg


def test_gemini_400_non_key_error_preserves_message(monkeypatch):
    class Resp:
        status_code = 400
        text = '{"error": {"message": "Invalid model name"}}'
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp())
    p = providers.GeminiProvider(Settings(provider="gemini", gemini_api_key="AIzaSyVALIDKEY123456", model="bad-model"))
    with pytest.raises(providers.ProviderError) as exc:
        p.complete("sys", "user")
    assert "Gemini returned an error (400)" in str(exc.value)


# ---------------------------------------------------------------------------
# 4. server.py: deduplication, lock safety, MIME magic bytes, RAM-only upload
# ---------------------------------------------------------------------------
def test_toggling_save_history_does_not_duplicate_repairs():
    c = TestClient(server.app, base_url="http://127.0.0.1")
    d = c.post("/api/diagnose", data={"description": "My toilet keeps running after I flush"}).json()
    rid = d["id"]
    c.post("/api/settings", json={"save_history": False})
    c.post(f"/api/repairs/{rid}/status", json={"status": "fixed"})
    repairs = c.get("/api/repairs").json()
    ids = [r["id"] for r in repairs]
    assert ids.count(rid) == 1


def test_ask_preserves_concurrent_status_and_answers_updates(monkeypatch):
    c = TestClient(server.app, base_url="http://127.0.0.1")
    d = c.post("/api/diagnose", data={"description": "My toilet keeps running after I flush"}).json()
    rid = d["id"]

    entered_llm = threading.Event()
    finish_llm = threading.Event()

    def slow_ask(*args, **kwargs):
        entered_llm.set()
        finish_llm.wait(timeout=5)
        return {"answer": "Use a 2-inch flapper.", "engine": "ollama"}

    monkeypatch.setattr(ai, "ask", slow_ask)

    t = threading.Thread(target=lambda: c.post("/api/ask", json={"repair_id": rid, "question": "which flapper?"}))
    t.start()
    assert entered_llm.wait(timeout=5)

    c.post(f"/api/repairs/{rid}/status", json={"status": "fixed"})
    c.post("/api/answers", json={"repair_id": rid, "answers": {"pattern": "All the time, never stops"}})
    assert c.get(f"/api/repairs/{rid}").json()["status"] == "fixed"

    finish_llm.set()
    t.join(timeout=5)

    after = c.get(f"/api/repairs/{rid}").json()
    assert after["status"] == "fixed"
    assert after["answers"] == {"pattern": "All the time, never stops"}
    assert len(after["chat"]) == 2


def test_riff_wave_rejected_as_image_png():
    c = TestClient(server.app, base_url="http://127.0.0.1")
    fake_wav = b"RIFF" + struct.pack("<I", 100) + b"WAVEfmt " + b"0" * 100
    files = {"photo": ("sound.png", io.BytesIO(fake_wav), "image/png")}
    r = c.post("/api/diagnose", data={"description": "toilet keeps running"}, files=files)
    assert r.status_code == 400


def test_photo_up_to_8mb_never_spools_to_disk(monkeypatch):
    rolled_to_disk = []
    orig_rollover = tempfile.SpooledTemporaryFile.rollover

    def spy_rollover(self):
        rolled_to_disk.append(True)
        return orig_rollover(self)

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "rollover", spy_rollover)
    c = TestClient(server.app, base_url="http://127.0.0.1")
    big_jpg = b"\xff\xd8\xff\xe0" + b"0" * (2 * 1024 * 1024)
    files = {"photo": ("photo.jpg", io.BytesIO(big_jpg), "image/jpeg")}
    r = c.post("/api/diagnose", data={"description": "toilet keeps running"}, files=files)
    assert r.status_code == 200
    assert rolled_to_disk == []
