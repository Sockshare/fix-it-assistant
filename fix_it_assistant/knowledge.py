"""The no-AI brain: a hand-written repair knowledge base plus a plain keyword matcher.

Schema of a problem (see kb/*.py):
    id, area, title
    anchors   - words that identify the THING (toilet, dryer...). At least one must appear
                for a confident match.
    keywords  - {phrase: weight} symptoms. Phrases may be several words.
    questions - [{id, q, options}] follow-ups shown to the user to narrow the cause.
    causes    - [{name, how_likely, signs, answers?}] ranked list; `answers` maps a
                question id to the option that points at this cause.
    safety    - {level: diy|careful|pro|emergency, notes: [...]}
    difficulty (1-5), time, cost {diy, pro}, tools [...], parts [{name, cost}],
    steps [...], call_pro_if [...], tip

The AI (if enabled) only ever picks from and explains these entries. Safety rules here
always win over anything a model says.
"""

from __future__ import annotations

import re

from .kb import appliances, electrical, home, outdoor, plumbing

AREAS = {
    "plumbing": {"label": "Plumbing & water", "emoji": "🚰"},
    "electrical": {"label": "Electrical & lights", "emoji": "💡"},
    "appliances": {"label": "Appliances", "emoji": "🧺"},
    "heating_cooling": {"label": "Heating & cooling", "emoji": "🌡️"},
    "doors_windows": {"label": "Doors & windows", "emoji": "🚪"},
    "walls_floors": {"label": "Walls, floors & bathroom", "emoji": "🧱"},
    "outdoor": {"label": "Outdoor & garage", "emoji": "🏡"},
    "car": {"label": "Car", "emoji": "🚗"},
    "bike": {"label": "Bike", "emoji": "🚲"},
}

SAFETY_LEVELS = {
    "diy": {"label": "Safe to try yourself", "emoji": "🟢", "blurb": "A normal person with basic tools can do this. Go slow and follow the steps."},
    "careful": {"label": "Doable, but be careful", "emoji": "🟡", "blurb": "Fine to try if you follow the safety notes exactly. If anything surprises you, stop and call someone."},
    "pro": {"label": "Call a professional", "emoji": "🟠", "blurb": "Only the simple checks are for you. The real repair needs a pro — it's dangerous or needs special tools."},
    "emergency": {"label": "Stop. Safety first.", "emoji": "🔴", "blurb": "Don't try to fix this. Follow the safety steps now, then call for help."},
}

PROBLEMS: list[dict] = plumbing.PROBLEMS + electrical.PROBLEMS + appliances.PROBLEMS + home.PROBLEMS + outdoor.PROBLEMS
BY_ID: dict[str, dict] = {p["id"]: p for p in PROBLEMS}

_REQUIRED = ("id", "area", "title", "anchors", "keywords", "questions", "causes", "safety", "difficulty", "time", "cost", "tools", "parts", "steps", "call_pro_if", "tip")


def validate() -> list[str]:
    """Return a list of schema problems (empty = good). Run by the tests."""
    errs = []
    seen = set()
    for p in PROBLEMS:
        for k in _REQUIRED:
            if k not in p:
                errs.append(f"{p.get('id', '?')}: missing {k}")
        if p["id"] in seen:
            errs.append(f"duplicate id {p['id']}")
        seen.add(p["id"])
        if p["area"] not in AREAS:
            errs.append(f"{p['id']}: unknown area {p['area']}")
        if p["safety"]["level"] not in SAFETY_LEVELS:
            errs.append(f"{p['id']}: unknown safety level")
        qids = {q["id"] for q in p["questions"]}
        for c in p["causes"]:
            for qid, opt in c.get("answers", {}).items():
                q = next((q for q in p["questions"] if q["id"] == qid), None)
                if q is None:
                    errs.append(f"{p['id']}: cause '{c['name'][:30]}' refers to unknown question {qid}")
                elif opt not in q["options"]:
                    errs.append(f"{p['id']}: cause '{c['name'][:30]}' answer '{opt}' not an option of {qid}")
        if not p["steps"] or not p["causes"]:
            errs.append(f"{p['id']}: needs steps and causes")
        if len(qids) != len(p["questions"]):
            errs.append(f"{p['id']}: duplicate question ids")
    return errs


# ---------------------------------------------------------------------------
# Emergencies: these are checked FIRST and shown no matter what else matches.
# ---------------------------------------------------------------------------
RED_FLAGS = [
    {
        "id": "gas_smell",
        "pattern": (
            r"\b(smell|smells|smelling|odou?r)\b.{0,50}\b(gas|propane|rotten egg|sulfur|sulphur)\b"
            r"|\b(gas|propane)\b.{0,50}\b(smell\w*|odou?r|leak\w*|hiss\w*)\b"
            r"|\b(hiss|hissing)\b.{0,50}\b(gas|propane|pipe|line|stove|meter|heater)\b"
            r"|\brotten eggs?\b|\bgas leak\b"
        ),
        "title": "Possible gas leak",
        "do": ["Don't flip any switches, light anything, or use the phone inside.", "Open a door on your way out and get everyone outside.", "From outside, call your gas company's emergency line or 911.", "Don't go back in until they say it's safe."],
    },
    {
        "id": "co_alarm",
        "pattern": (
            r"\b(carbon monoxide|co)\s*(alarm|detector|sensor)?\b.{0,50}\b(going off|sounding|beeping|loud|4 beeps|four beeps|triggered|warning|leak|poison\w*|high)\b"
            r"|\b(going off|sounding|loud|4 beeps|four beeps)\b.{0,50}\b(carbon monoxide|co alarm|co detector)\b"
            r"|\bfour beeps\b|\b4 beeps\b"
            r"|\b(headache|dizzy|dizziness|nause\w*)\b.{0,70}\b(furnace|heater|boiler|generator|car running|carbon monoxide|fumes)\b"
            r"|\b(furnace|heater|boiler|generator|car running)\b.{0,70}\b(headache|dizzy|dizziness|nause\w*)\b"
        ),
        "title": "Carbon monoxide warning",
        "do": ["Get everyone, including pets, outside into fresh air right now.", "Call 911 or your fire department from outside.", "Don't go back in to open windows or find the source.", "Anyone with a headache, dizziness, or nausea should be checked by medical staff."],
    },
    {
        "id": "electrical_fire",
        "pattern": (
            r"\b(spark\w*|arcing|smok\w*|burning smell|smells? (like )?(burning|burnt|melting|hot plastic|fishy)|scorch\w*|melt\w*)\b.{0,60}\b(outlet|socket|switch|panel|breaker|fuse box|wire|wiring|cord|plug|fixture|light|lamp|fan|wall|ceiling)\b"
            r"|\b(outlet|socket|switch|panel|breaker|fuse box|wire|wiring|cord|plug|fixture|light|lamp|ceiling light)\b.{0,60}\b(spark\w*|arcing|smok\w*|burning|burnt|scorch\w*|melt\w*|hot to the touch|on fire|flames?)\b"
        ),
        "title": "Electrical fire risk",
        "do": ["Turn off the breaker for that circuit (or the main breaker) now.", "Unplug nothing that's hot or smoking — leave it, cut the power at the panel.", "If you see flames: get out and call 911. Never use water on an electrical fire.", "Don't use that outlet, switch, or device again until an electrician has looked at it."],
    },
    {
        "id": "water_electric",
        "pattern": (
            r"\b(water|flood\w*|leak\w*|wet|puddle|dripping|soak\w*)\b.{0,60}\b(outlet|socket|breaker panel|electrical panel|fuse box|light fixture|ceiling light|wires|wiring)\b"
            r"|\b(outlet|socket|electrical panel|breaker panel|fuse box|light fixture|ceiling light|wires|wiring)\b.{0,60}\b(water|flood\w*|leak\w*|wet|puddle|dripping|soak\w*)\b"
        ),
        "title": "Water near electricity",
        "do": ["Don't step in the water if it could be touching anything electrical.", "If you can reach the panel safely and dry, turn off the affected breaker or the main.", "If you can't reach it safely, call an electrician or the fire department.", "Only then deal with the water."],
    },
    {
        "id": "sewage",
        "pattern": r"\b(sewage|sewer|raw waste|poop|feces|faeces)\b.{0,60}\b(backing up|back up|backed up|coming up|overflow\w*|flood\w*|in the (tub|bathtub|shower|basement|floor))|\b(tub|bathtub|shower|basement|floor drain)\b.{0,50}\b(sewage|sewer|black water|raw waste)",
        "title": "Sewage backup",
        "do": ["Stop using all water in the house — every flush and shower makes it worse.", "Keep kids and pets out of the area. Sewage carries disease.", "Call a plumber today (this is a main line blockage) and your city if the street is also backing up.", "Wear gloves, boots and a mask for any cleanup; throw away anything porous it touched."],
    },
    {
        "id": "ceiling_sag",
        "pattern": (
            r"\b(ceiling)\b.{0,60}\b(sag\w*|bulg\w*|bow\w*|caving|collaps\w*|falling|about to (fall|collapse)|dripping|water stain spreading)\b"
            r"|\b(bulg\w*|sag\w*|bow\w*|caving|collaps\w*)\b.{0,50}\bceiling\b"
        ),
        "title": "Ceiling holding water or sagging",
        "do": ["Move people, pets, and anything valuable out from under it right now — wet drywall is heavy and can fall without warning.", "Turn off the water main if the leak is from plumbing above, and turn off the breaker for any lights or fans in that ceiling.", "Put buckets or tarps on the floor from a safe distance, but do NOT stand directly under a sagging bulge to poke it.", "Call a plumber (if a pipe leaked) or a roofer/contractor to drain and remove the damaged drywall safely."],
    },
    {
        "id": "burst_pipe",
        "pattern": r"\b(burst\w*|broke|broken|split|ruptur\w*)\b.{0,25}\bpipes?\b|\bpipes?\b.{0,25}\b(burst\w*|broke|broken|split|ruptur\w*)\b|\bwater (gushing|pouring|spraying)\b|\bflooding\b",
        "title": "Burst pipe or active flooding",
        "do": ["Turn off the main water shutoff NOW. It's usually where the water line enters the house: near the meter, in the basement, a crawlspace, or by the water heater.", "If water is near outlets or the panel, cut the power at the main breaker first — only if you can do that without standing in water.", "Open the lowest faucet in the house to drain the pipes.", "Call a plumber. Then start moving things and soaking up water; mold starts in 24–48 hours."],
    },
    {
        "id": "structural",
        "pattern": (
            r"\b(foundation|basement wall)\b.{0,50}\b(crack\w*|bow\w*|buckl\w*|lean\w*|shift\w*|sink\w*|settling)\b"
            r"|\b(crack\w*|bow\w*|buckl\w*|lean\w*|shift\w*)\b.{0,50}\b(foundation|basement wall|load-bearing|main beam|support beam|joist)\b"
            r"|\b(wide|big|large|huge|growing|getting bigger|stair-?step\w*|horizontal|diagonal)\b.{0,35}\bcrack\w*\b.{0,45}\b(foundation|wall|beam|floor|brick|basement)\b"
            r"|\b(foundation|wall|beam|floor|brick|basement)\b.{0,45}\b(wide|big|large|huge|growing|getting bigger|stair-?step\w*|horizontal)\b.{0,35}\bcrack\w*\b"
            r"|\b(foundation|wall|beam|floor)\b.{0,40}\b(crack\w*)\b.{0,40}\b(wide|big|large|growing|getting bigger|stair\w*|horizontal|bow\w*)\b"
            r"|\bbow\w*\b.{0,30}\bwall\b|\bwall\b.{0,30}\bbow\w*\b"
            r"|\b(beam|floor|house|foundation)\b.{0,35}\b(sag\w*|sinking|sloping badly|dropped|buckl\w*|caving)\b"
            r"|\b(sag\w*|sinking|buckl\w*)\b.{0,35}\b(beam|floor|foundation)\b"
        ),
        "title": "Possible structural problem",
        "do": ["This isn't a patch job. Take photos with a ruler for scale and note the date.", "Keep weight off the area if a floor or beam is sagging or sinking.", "Call a licensed structural engineer (not a basement-salesperson who sells repairs) for an independent assessment — usually $300–600 and worth every penny.", "If a wall or ceiling is actively moving or you hear cracking sounds, leave the building and call the fire department."],
    },
]
_RED = [(re.compile(r["pattern"], re.I), r) for r in RED_FLAGS]


def red_flags(text: str) -> list[dict]:
    text = " ".join((text or "").split())[:5000]
    out = []
    for rx, r in _RED:
        if rx.search(text):
            out.append({"id": r["id"], "title": r["title"], "do": r["do"]})
    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9][a-z0-9'/-]*")

_TYPOS = {
    "dishwahser": "dishwasher",
    "dishwaser": "dishwasher",
    "diswasher": "dishwasher",
    "refridgerator": "refrigerator",
    "refrigirator": "refrigerator",
    "refigerator": "refrigerator",
    "refrigerater": "refrigerator",
    "frig": "fridge",
    "frige": "fridge",
    "tiolet": "toilet",
    "tolet": "toilet",
    "toliet": "toilet",
    "disposel": "disposal",
    "disposall": "disposal",
    "garbge": "garbage",
    "draning": "draining",
    "drianing": "draining",
    "drian": "drain",
    "botom": "bottom",
    "humms": "hums",
    "humm": "hum",
    "runing": "running",
    "leeking": "leaking",
    "fauset": "faucet",
    "faust": "faucet",
    "furnice": "furnace",
    "thermastat": "thermostat",
    "circut": "circuit",
    "braker": "breaker",
    "micowave": "microwave",
    "wahser": "washer",
}

_OUT_OF_SCOPE = {
    "printer", "laptop", "computer", "pc", "macbook", "iphone", "android", "phone",
    "smartphone", "tablet", "ipad", "router", "modem", "wifi", "wi-fi", "television",
    "tv", "monitor", "keyboard", "mouse", "playstation", "xbox", "nintendo",
    "lawnmower", "mower", "chainsaw", "weedwacker", "tractor",
}

_HOUSEHOLD_NOUNS = {
    "toilet", "sink", "faucet", "tap", "pipe", "pipes", "drain", "shower", "tub", "bathtub",
    "disposal", "heater", "outlet", "socket", "breaker", "switch", "dimmer", "doorbell",
    "fan", "fridge", "refrigerator", "freezer", "dishwasher", "washer", "dryer", "oven",
    "stove", "range", "microwave", "furnace", "ac", "thermostat", "radiator", "door",
    "window", "cabinet", "drawer", "drywall", "wall", "floor", "tile", "caulk", "gutter",
    "downspout", "sprinkler", "garage", "car", "truck", "tire", "tires", "bike", "bicycle",
}


def _norm(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    for bad, good in (
        ("doesnt", "doesn't"),
        ("wont", "won't"),
        ("cant", "can't"),
        ("isnt", "isn't"),
        ("arent", "aren't"),
        ("didnt", "didn't"),
        ("hasnt", "hasn't"),
        ("stairstep", "stair-step"),
    ):
        text = re.sub(rf"\b{bad}\b", good, text)
    # Normalize OBD-II fault codes like P0420 -> p0420 p0 code
    text = re.sub(r"\b(p0\d{3})\b", r"\1 p0", text)
    text = re.sub(r"[^a-z0-9'/\s-]", " ", text)
    words = [_TYPOS.get(w, w) for w in text.split()]
    return " " + " ".join(words)[:4000] + " "


def _stem(w: str) -> str:
    w = _TYPOS.get(w, w)
    if len(w) <= 3 or w in ("gas", "has", "was", "yes", "his", "bus", "ac", "a/c"):
        return w
    # Protect words where -ing is the root or changes meaning ("sinking" != "sink" the plumbing fixture)
    if w in ("sinking", "ceiling", "wiring", "flooring", "lighting", "spring", "string", "thing", "ring", "bring", "swing"):
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ing") and len(w) > 5:
        base = w[:-3]
        if len(base) >= 3 and base[-1] == base[-2] and base[-1] not in "lsz":
            return base[:-1]
        return base.rstrip("e") if len(base) > 3 else base
    if w.endswith("ed") and len(w) > 4:
        base = w[:-2]
        if len(base) >= 3 and base[-1] == base[-2] and base[-1] not in "lsz":
            return base[:-1]
        return base.rstrip("e") if len(base) > 3 else base
    if re.search(r"(sh|ch|x|z|ss)es$", w) and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")) and len(w) >= 4:
        base = w[:-1]
        return base.rstrip("e") if len(base) > 3 else base
    if w.endswith("e") and len(w) >= 4:
        return w[:-1]
    return w


def _phrase_in(phrase: str, text: str, stems: set[str]) -> bool:
    phrase = phrase.lower()
    if " " in phrase or "'" in phrase or "-" in phrase:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text))
    # Don't let the verb phrase "won't light" / "not light" match the electrical noun "light"
    if phrase in ("light", "lights") and re.search(r"\b(pilot|burner|stove|oven|igniter|furnace|heater|grill)\b|\b(won't|doesn't|not|no)\s+light\b", text):
        if not re.search(r"\b(bulb|lamp|fixture|ceiling light|flicker|dim|switch)\b", text):
            return False
    if f" {phrase} " in text:
        return True
    return _stem(phrase) in stems


def match(description: str, area_hint: str = "", photo_text: str = "", limit: int = 3) -> dict:
    """Rank knowledge-base problems against a plain-language description.

    Returns {"candidates": [{id, title, area, score, confidence, matched}], "flags": [...],
             "confident": bool, "area_hint": str}
    """
    raw = f"{description or ''}\n{photo_text or ''}"
    text = _norm(raw)
    tokens = _WORD.findall(text)
    stems = {_stem(w) for w in tokens}
    flags = red_flags(raw)
    has_oos = any(w in _OUT_OF_SCOPE for w in tokens)
    if has_oos and not (set(tokens) & _HOUSEHOLD_NOUNS):
        return {"candidates": [], "flags": flags, "confident": False, "area_hint": area_hint}
    scored = []
    for p in PROBLEMS:
        anchors_hit = [a for a in p["anchors"] if _phrase_in(a, text, stems)]
        raw_kw_hits = [(k, w) for k, w in p["keywords"].items() if _phrase_in(k, text, stems)]
        dedup_kw: dict[tuple[str, ...], tuple[str, float]] = {}
        for k, w in raw_kw_hits:
            key = tuple(_stem(tok) for tok in _WORD.findall(k.lower())) or (k,)
            if key not in dedup_kw or w > dedup_kw[key][1]:
                dedup_kw[key] = (k, w)
        kw_hits = list(dedup_kw.values())
        if not anchors_hit and not kw_hits:
            continue  # an area hint alone is not evidence
        # If the user explicitly named an out-of-scope device (printer, laptop, phone, lawnmower...)
        # and did not name any anchor of this household problem, skip it.
        if has_oos and not anchors_hit:
            continue
        # If an emergency red-flag fired, don't return weak symptom-only guesses (like "smell" -> check_engine)
        if flags and not anchors_hit:
            continue
        score = min(len(anchors_hit), 2) * 3.0 + sum(w for _, w in kw_hits)
        if not anchors_hit:
            score *= 0.35  # symptoms without the thing: weak evidence
        if area_hint and p["area"] == area_hint:
            score += 3.0
        elif area_hint and area_hint in AREAS and p["area"] != area_hint:
            score *= 0.6
        # If an emergency flag is active, require a genuinely strong match (score >= 6) so we don't show random DIY cards under a life-safety banner
        if flags and score < 6.0:
            continue
        if score > 0:
            lvl = p["safety"]["level"]
            if p["id"] == "garage_door_wont_close" and re.search(r"\b(spring|torsion|cable|snapped|bang|heavy)\b", text):
                lvl = "pro"
            if flags:
                lvl = "emergency"
            scored.append((score, p, anchors_hit, [k for k, _ in kw_hits], lvl))
    scored.sort(key=lambda t: (-t[0], t[1]["title"]))
    top = scored[0][0] if scored else 0.0
    out = []
    for score, p, anchors_hit, kws, lvl in scored[:limit]:
        if top >= 8:
            conf = "high" if score >= top * 0.8 else "medium" if score >= top * 0.5 else "low"
        elif top >= 5:
            conf = "medium" if score >= top * 0.8 else "low"
        else:
            conf = "low"
        out.append({"id": p["id"], "title": p["title"], "area": p["area"], "area_label": AREAS[p["area"]]["label"], "score": round(score, 1), "confidence": conf, "matched": sorted(set(anchors_hit + kws))[:8], "safety_level": lvl})
    return {"candidates": out, "flags": flags, "confident": bool(out) and out[0]["confidence"] != "low", "area_hint": area_hint}


def rank_causes(problem: dict, answers: dict | None, description: str = "") -> list[dict]:
    """Re-order a problem's causes using the user's follow-up answers.
    A cause whose `answers` all agree moves up; one that disagrees moves down; unknown stays."""
    answers = {k: v for k, v in (answers or {}).items() if v}
    order = {"most likely": 0, "likely": 1, "possible": 2}
    desc_low = (description or "").lower()
    ranked = []
    for i, c in enumerate(problem["causes"]):
        want = c.get("answers", {})
        agree = sum(1 for q, opt in want.items() if answers.get(q) == opt)
        disagree = sum(1 for q, opt in want.items() if q in answers and answers.get(q) != opt)
        # If the user explicitly mentioned a broken/snapped spring on a garage door, promote the torsion spring cause to #1
        if problem["id"] == "garage_door_wont_close" and "spring" in c["name"].lower() and re.search(r"\b(spring|torsion|snapped|loud bang)\b", desc_low):
            agree += 2
        base = order.get(c.get("how_likely", "possible"), 2)
        key = (-agree + disagree * 2, base, i)
        tag = "fits your answers" if agree and not disagree else ("less likely from your answers" if disagree else "")
        ranked.append((key, {**c, "note": tag}))
    ranked.sort(key=lambda t: t[0])
    return [c for _, c in ranked]


def browse() -> list[dict]:
    """Everything, grouped by area, for the 'Browse all problems' screen."""
    out = []
    for key, meta in AREAS.items():
        items = [{"id": p["id"], "title": p["title"], "safety_level": p["safety"]["level"], "difficulty": p["difficulty"]} for p in PROBLEMS if p["area"] == key]
        if items:
            out.append({"area": key, "label": meta["label"], "emoji": meta["emoji"], "problems": sorted(items, key=lambda x: x["title"])})
    return out


def card(problem_id: str, answers: dict | None = None, description: str = "", has_flags: bool = False) -> dict | None:
    """A problem rendered for the UI, with causes ranked by the user's answers."""
    p = BY_ID.get(problem_id)
    if not p:
        return None
    lvl = p["safety"]["level"]
    notes = list(p["safety"].get("notes", []))
    diff = p["difficulty"]
    if problem_id == "garage_door_wont_close" and re.search(r"\b(spring|torsion|cable|snapped|loud bang)\b", (description or "").lower()):
        lvl = "pro"
        diff = 5
    if has_flags:
        lvl = "emergency"
    return {
        **{k: p[k] for k in ("id", "area", "title", "questions", "time", "cost", "tools", "parts", "steps", "call_pro_if", "tip")},
        "difficulty": diff,
        "area_label": AREAS[p["area"]]["label"],
        "area_emoji": AREAS[p["area"]]["emoji"],
        "safety": {"level": lvl, "notes": notes, **SAFETY_LEVELS[lvl]},
        "causes": rank_causes(p, answers, description=description),
    }
