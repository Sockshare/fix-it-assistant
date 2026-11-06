"""G1 — unit tests: knowledge base integrity, matcher accuracy, red flags, cause ranking, AI guards."""

from __future__ import annotations

import re

import pytest

from fix_it_assistant import ai, knowledge as K
from fix_it_assistant.config import Settings


# --------------------------------------------------------------------- schema
def test_kb_schema_is_valid():
    assert K.validate() == []


def test_kb_has_broad_coverage():
    assert len(K.PROBLEMS) >= 40
    areas = {p["area"] for p in K.PROBLEMS}
    assert {"plumbing", "electrical", "appliances", "heating_cooling", "doors_windows", "walls_floors", "outdoor", "car"} <= areas


def test_every_problem_is_plainly_written_and_complete():
    for p in K.PROBLEMS:
        assert 3 <= len(p["steps"]) <= 8, p["id"]
        assert 2 <= len(p["causes"]) <= 7, p["id"]
        assert p["safety"]["notes"], p["id"]
        assert p["cost"]["diy"] and p["cost"]["pro"], p["id"]
        assert 1 <= p["difficulty"] <= 5, p["id"]
        for st in p["steps"]:
            assert len(st) < 600, f"{p['id']} step too long"
        # dangerous categories must not be marked plain DIY
        if p["id"] in ("microwave_dead",):
            assert p["safety"]["level"] == "pro"
        if p["area"] == "electrical" and p["id"] not in ("doorbell_dead", "smoke_alarm_chirp", "ceiling_fan_wobble"):
            assert p["safety"]["level"] in ("careful", "pro"), p["id"]


def test_pro_and_careful_entries_always_have_call_pro_triggers():
    for p in K.PROBLEMS:
        if p["safety"]["level"] in ("careful", "pro"):
            assert p["call_pro_if"], p["id"]


# -------------------------------------------------------------------- matcher
@pytest.mark.parametrize("text,expected", [
    ("My toilet keeps running after I flush, hissing from the tank", "toilet_running"),
    ("the kitchen sink is draining really slow and smells bad", "drain_slow"),
    ("dryer runs but clothes are still damp after two cycles", "dryer_not_heating"),
    ("outlet in the bathroom stopped working", "outlet_dead"),
    ("my car makes a rapid clicking noise when I turn the key and the lights are dim", "car_wont_start"),
    ("water pooling under the crisper drawers in my fridge", "fridge_leaking"),
    ("bedroom door rubs at the top and I have to push hard to close it", "door_sticks"),
    ("ac is blowing warm air and there is ice on the copper pipe", "ac_not_cooling"),
    ("black spots on the bathroom ceiling", "bathroom_mold"),
    ("garage door starts to close then goes back up and the light blinks", "garage_door_wont_close"),
    ("the smoke alarm chirps every minute at night", "smoke_alarm_chirp"),
    ("breaker keeps tripping when I run the space heater", "breaker_trips"),
    ("dishwasher has standing water in the bottom and won't drain", "dishwasher_not_draining"),
    ("furnace blows cold air and then shuts off after a minute", "furnace_no_heat"),
    ("screw just spins in the cabinet hinge and won't tighten", "stripped_screw_hole"),
    ("the tire pressure light came on this cold morning", "tire_pressure_light"),
    ("radiator in the back bedroom is cold at the top and gurgles", "radiator_cold"),
])
def test_matcher_top_pick(text, expected):
    m = K.match(text)
    assert m["candidates"], text
    assert m["candidates"][0]["id"] == expected, (text, [c["id"] for c in m["candidates"]])
    assert m["confident"]


def test_matcher_is_honest_when_it_does_not_know():
    m = K.match("my printer is jammed")
    assert not m["confident"]
    m = K.match("it's broken", area_hint="plumbing")
    assert not m["confident"] and all(c["confidence"] == "low" for c in m["candidates"])
    assert K.match("help", area_hint="plumbing")["candidates"] == []  # an area hint alone is never a match
    assert K.match("")["candidates"] == []


def test_area_hint_breaks_ties_without_inventing_matches():
    m = K.match("it leaks", area_hint="appliances")
    assert all(c["area"] == "appliances" for c in m["candidates"][:1]) or not m["candidates"]
    assert all(c["matched"] for c in m["candidates"])


def test_matcher_handles_garbage_and_huge_input():
    big = "toilet " * 5000 + "\x00\x01" + "🙂" * 100
    m = K.match(big)
    assert m["candidates"][0]["area"] == "plumbing"
    assert K.match("!!!???")["candidates"] == []


# ------------------------------------------------------------------ red flags
@pytest.mark.parametrize("text,flag", [
    ("I smell gas near the stove", "gas_smell"),
    ("there's a rotten egg smell by the water heater", "gas_smell"),
    ("the outlet is sparking and smells like burning plastic", "electrical_fire"),
    ("my CO alarm is going off", "co_alarm"),
    ("sewage is backing up into the tub", "sewage"),
    ("the ceiling is bulging and dripping water", "ceiling_sag"),
    ("a pipe burst in the basement and water is pouring out", "burst_pipe"),
    ("water is leaking onto the breaker panel", "water_electric"),
])
def test_red_flags_fire(text, flag):
    ids = [f["id"] for f in K.red_flags(text)]
    assert flag in ids, (text, ids)


def test_red_flags_do_not_cry_wolf():
    for t in ["toilet keeps running", "gas stove burner won't light", "the dryer smells a little musty", "water under the sink", "smoke alarm chirps", "my lights flicker"]:
        assert K.red_flags(t) == [], t


def test_match_returns_flags_even_with_no_candidates():
    m = K.match("I smell gas")
    assert m["flags"] and m["flags"][0]["id"] == "gas_smell"


# ------------------------------------------------------------- cause ranking
def test_answers_reorder_causes_and_tag_them():
    p = K.BY_ID["toilet_running"]
    base = [c["name"] for c in K.rank_causes(p, {})]
    assert base[0].startswith("Worn flapper")
    ranked = K.rank_causes(p, {"pattern": "All the time, never stops"})
    assert ranked[0]["name"].startswith("Water level") and ranked[0]["note"] == "fits your answers"
    assert any(c["note"] == "less likely from your answers" for c in ranked)
    # an unknown answer value is ignored, not an error
    assert [c["name"] for c in K.rank_causes(p, {"pattern": "nonsense", "bogus": "x"})] == base


def test_card_and_browse():
    c = K.card("outlet_dead", {"others": "Yes"})
    assert c["safety"]["label"] and c["area_label"] and c["causes"][0]["name"].startswith("Tripped breaker")
    assert K.card("nope") is None
    b = K.browse()
    assert sum(len(a["problems"]) for a in b) == len(K.PROBLEMS)


# ------------------------------------------------------------------ AI guards
def _settings(provider="offline"):
    return Settings(provider=provider)


def test_offline_explain_uses_rules_and_never_invents():
    m = K.match("toilet keeps running and hissing")
    e = ai.explain("toilet keeps running and hissing", "", m["candidates"], _settings())
    assert e["pick"] == "toilet_running" and e["engine"] == "rules" and "Toilet keeps running" in e["why"]
    e = ai.explain("printer jammed", "", [], _settings())
    assert e["pick"] is None and "browse" in e["why"].lower()


def test_offline_photo_is_refused_politely():
    r = ai.describe_photo([(b"x" * 200, "image/png")], _settings())
    assert r["text"] == "" and "words" in r["error"]


def test_offline_ask_answers_simple_questions():
    for q, needle in [("what tools do I need?", "gloves"), ("how much will it cost", "$5"), ("is it safe to do myself?", "Safe to try"), ("when should I call a plumber", "floor"), ("how long does it take", "15")]:
        a = ai.ask("toilet_running", "toilet running", q, [], _settings())
        assert needle.lower() in a["answer"].lower(), (q, a["answer"])
    a = ai.ask("toilet_running", "", "what is the meaning of life", [], _settings())
    assert "simple questions" in a["answer"]
    assert "lost track" in ai.ask("nope", "", "hi", [], _settings())["answer"]


class _FakeProvider:
    name = "fake"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, user, want_json=False, images=None):
        self.calls.append((system, user, images))
        return self.reply


def test_explain_model_cannot_pick_outside_candidates(monkeypatch):
    m = K.match("toilet keeps running")
    fake = _FakeProvider('{"pick": "car_wont_start", "why": "It is definitely your car battery, very clearly, trust me.", "question": ""}')
    monkeypatch.setattr(ai, "get_provider", lambda s: fake)
    e = ai.explain("toilet keeps running", "", m["candidates"], _settings("ollama"))
    assert e["pick"] == "toilet_running"  # fell back to the rules' top pick
    fake.reply = '{"pick": "none", "why": "None of these fit what you describe at all."}'
    assert ai.explain("toilet keeps running", "", m["candidates"], _settings("ollama"))["pick"] is None


def test_explain_model_cannot_soften_a_pro_level_card(monkeypatch):
    m = K.match("microwave runs but doesn't heat")
    assert m["candidates"][0]["id"] == "microwave_dead"
    fake = _FakeProvider('{"pick": "microwave_dead", "why": "This is an easy DIY fix, just open the case yourself and swap the fuse.", "question": ""}')
    monkeypatch.setattr(ai, "get_provider", lambda s: fake)
    e = ai.explain("microwave runs but doesn't heat", "", m["candidates"], _settings("ollama"))
    assert "easy" not in e["why"].lower() and "yourself" not in e["why"].lower()


def test_ask_prepends_warning_when_model_encourages_diy_on_pro_card(monkeypatch):
    fake = _FakeProvider("Sure, you can open it up yourself, it's easy.")
    monkeypatch.setattr(ai, "get_provider", lambda s: fake)
    a = ai.ask("microwave_dead", "microwave dead", "can I open it?", [], _settings("ollama"))
    assert a["answer"].startswith("⚠️")
    # prompt contains the card but nothing else private
    assert "REPAIR CARD" in fake.calls[0][1]


def test_ask_falls_back_when_provider_errors(monkeypatch):
    from fix_it_assistant.llm.providers import ProviderError

    class Boom(_FakeProvider):
        def complete(self, *a, **k):
            raise ProviderError("Could not reach Ollama. Is it running? Try: ollama serve")

    monkeypatch.setattr(ai, "get_provider", lambda s: Boom(""))
    a = ai.ask("toilet_running", "", "what tools?", [], _settings("ollama"))
    assert a["engine"] == "rules" and "gloves" in a["answer"].lower() and "ollama serve" in a["error"]


def test_describe_photo_passes_images_and_trims(monkeypatch):
    fake = _FakeProvider("A white toilet tank with the lid off. " * 80)
    monkeypatch.setattr(ai, "get_provider", lambda s: fake)
    r = ai.describe_photo([(b"\x89PNG" + b"0" * 300, "image/png")], _settings("gemini"))
    assert fake.calls[0][2] and len(r["text"]) <= 1201 and r["engine"] == "fake"


# ------------------------------------------------------------- providers
def test_providers_accept_images_argument():
    from fix_it_assistant.llm.providers import GeminiProvider, OllamaProvider, OpenAIProvider

    s = Settings(provider="ollama")
    assert OllamaProvider(s).vision_model() == "moondream"
    s.vision_model = "llava"
    assert OllamaProvider(s).vision_model() == "llava"
    assert OpenAIProvider(Settings(provider="openai")).vision_model() == "gpt-4o-mini"
    assert GeminiProvider(Settings(provider="gemini")).vision_model() == "gemini-2.0-flash"


def test_readable_text_in_kb():
    """No internal jargon leaks: steps must not contain TODO/FIXME or markdown headers."""
    for p in K.PROBLEMS:
        blob = " ".join(p["steps"] + [c["name"] for c in p["causes"]] + p["safety"]["notes"])
        assert not re.search(r"\b(TODO|FIXME|lorem)\b|^#", blob, re.I), p["id"]
