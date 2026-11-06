"""The optional AI layer. Three jobs, each with a no-AI fallback:

    describe_photo()  - a vision model turns a photo into a few factual sentences
    explain()         - the model picks the best knowledge-base match and explains it in plain words
    ask()             - a grounded Q&A about the CURRENT repair card only

Rules that beat the model, always:
  * It can only pick from the candidate ids we give it. Never invents a diagnosis.
  * Red-flag emergencies from knowledge.py are shown regardless of what it says.
  * Safety level and "call a pro if" come from the knowledge base, never from the model.
"""

from __future__ import annotations

import json
import re

from .config import Settings
from .knowledge import BY_ID, SAFETY_LEVELS, card
from .llm.providers import ProviderError, extract_json, get_provider

MAX_PHOTO_BYTES = 8 * 1024 * 1024

PHOTO_SYSTEM = (
    "You describe photos of household problems for a repair helper. Be factual and specific. "
    "Say what object it is (e.g. a toilet tank, an outlet, a dryer vent), what looks wrong (water, rust, cracks, "
    "scorch marks, mineral scale, gaps, frayed parts), and any visible labels or model numbers. "
    "Use 3 to 5 short sentences. Do NOT give repair advice. Do NOT guess at causes. If the photo is unclear, say so."
)

EXPLAIN_SYSTEM = (
    "You are a friendly, careful home-repair helper talking to someone with no experience. "
    "You get: the person's description, an optional photo description, and a short list of CANDIDATE problems from a "
    "trusted repair guide. Pick the ONE candidate id that fits best, or \"none\" if none fit. "
    "Then write 2–4 short plain sentences (reading level: a 10-year-old) telling them what it most likely is and why "
    "you think so, using details THEY mentioned. Never contradict the safety level given. Never invent a cause that "
    "isn't in the candidates. Optionally suggest ONE clarifying question that would help, if the answer isn't obvious. "
    "Reply ONLY with JSON: {\"pick\": \"<id or none>\", \"why\": \"...\", \"question\": \"...\" or \"\"}"
)

ASK_SYSTEM = (
    "You are a friendly home-repair helper. You have ONE repair guide card (JSON below) for the problem the person is "
    "working on. Answer their question in plain words (reading level: a 10-year-old), in 1–5 short sentences or a "
    "short numbered list. Use the card as your source. You may add common-sense general knowledge about tools and "
    "household items, but: never tell them to do something the card marks as 'call a pro'; never downplay a safety "
    "note; if the question is about a different problem or goes beyond what you can know without seeing it, say so "
    "and suggest they start a new check or call a professional. No made-up part numbers or prices beyond the card."
)


def _provider(settings: Settings):
    return get_provider(settings)


# ---------------------------------------------------------------------------
# Photo
# ---------------------------------------------------------------------------
def describe_photo(images: list[tuple[bytes, str]], settings: Settings) -> dict:
    """Returns {"text": description, "engine": name} or {"text": "", "error": friendly message}."""
    try:
        provider = _provider(settings)
    except Exception as exc:  # noqa: BLE001
        return {"text": "", "engine": settings.provider, "error": f"AI settings error: {exc}"}
    if provider.name == "offline":
        return {"text": "", "engine": "offline", "error": "Photos need an AI option turned on. Describe what you see in words instead — that works just as well."}
    try:
        text = provider.complete(PHOTO_SYSTEM, "Describe this photo of a household problem.", want_json=False, images=images)
    except ProviderError as exc:
        return {"text": "", "engine": provider.name, "error": str(exc)}
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) > 1200:
        text = text[:1200].rsplit(". ", 1)[0] + "."
    return {"text": text, "engine": provider.name}


# ---------------------------------------------------------------------------
# Explain / pick
# ---------------------------------------------------------------------------
def _template_explain(description: str, candidates: list[dict]) -> dict:
    if not candidates:
        return {"pick": None, "why": "I couldn't match that to anything in the guide. Try adding what the thing is (toilet, dryer, outlet…) and what it's doing, or browse all problems.", "question": "", "engine": "rules"}
    top = candidates[0]
    terms = [t for t in top.get("matched", []) if len(t) > 2][:4]
    because = f" because you mentioned {', '.join(terms)}" if terms else ""
    if top.get("confidence") == "high":
        why = f"This looks like **{top['title']}**{because}. The steps below start with the most common cause."
    elif top.get("confidence") == "medium":
        why = f"This is probably **{top['title']}**{because}. If that doesn't sound right, check the other matches."
    else:
        why = f"My best guess is **{top['title']}**{because}, but I'm not sure. Answer the questions below or add more detail."
    p = BY_ID[top["id"]]
    q = p["questions"][0]["q"] if p["questions"] else ""
    return {"pick": top["id"], "why": why, "question": q, "engine": "rules"}


_HAZARD_DIY_RE = re.compile(
    r"\b(replace|adjust|wind|unwind|tighten|loosen|open|repair)\b[^.]{0,50}\b(torsion spring|garage[^\s]* spring|capacitor|magnetron|refrigerant|freon|gas valve|main panel|meter)\b",
    re.I,
)


def explain(description: str, photo_text: str, candidates: list[dict], settings: Settings, has_flags: bool = False) -> dict:
    """Pick the best candidate and explain it. The model may only choose from `candidates`."""
    fallback = _template_explain(description, candidates)
    try:
        provider = _provider(settings)
    except Exception as exc:  # noqa: BLE001
        return {**fallback, "error": f"AI settings error: {exc}"}
    if provider.name == "offline" or not candidates:
        return fallback
    cand_txt = "\n".join(f"- id={c['id']}: {c['title']} (safety: {SAFETY_LEVELS[BY_ID[c['id']]['safety']['level']]['label']}; matched: {', '.join(c.get('matched', [])[:6])})" for c in candidates)
    user = f"DESCRIPTION:\n{description[:3000]}\n\nPHOTO (if any):\n{photo_text[:1200] or 'none'}\n\nCANDIDATES:\n{cand_txt}"
    try:
        raw = provider.complete(EXPLAIN_SYSTEM, user, want_json=True)
    except ProviderError as exc:
        return {**fallback, "error": str(exc)}
    data = extract_json(raw)
    if not isinstance(data, dict):
        return fallback
    raw_pick = data.get("pick")
    pick = str(raw_pick).strip() if raw_pick is not None else ""
    allowed = {c["id"] for c in candidates}
    if pick not in allowed:
        pick = None if pick.lower() == "none" else fallback["pick"]
    why = re.sub(r"\s+", " ", str(data.get("why") or "")).strip()
    if not why or len(why) < 20:
        why = fallback["why"]
    # The model must not weaken safety language. If it says "safe"/"easy" about a pro/emergency card or hazardous part, replace.
    c_obj = card(pick, description=description, has_flags=has_flags) if pick else None
    eff_level = "emergency" if has_flags else (c_obj["safety"]["level"] if c_obj else "diy")
    if (eff_level in ("pro", "emergency") and re.search(r"\b(easy|simple|safe to|yourself|diy)\b", why, re.I)) or _HAZARD_DIY_RE.search(why):
        why = fallback["why"] if fallback["pick"] == pick else (f"This looks like **{c_obj['title']}**. Read the safety box first — this one is not a do-it-yourself job." if c_obj else fallback["why"])
    question = re.sub(r"\s+", " ", str(data.get("question") or "")).strip()[:200]
    return {"pick": pick, "why": why[:700], "question": question, "engine": provider.name}


# ---------------------------------------------------------------------------
# Grounded Q&A
# ---------------------------------------------------------------------------
_TEMPLATE_ANSWERS = [
    (r"\b(first|start|begin|step 1|do first|where do i start)\b", lambda c: "Start here: " + c["steps"][0]),
    (
        r"\b(size|part|parts|flapper|filter|replacement|which one)\b",
        lambda c: (("Parts for this job: " + "; ".join(f"{p['name']} ({p['cost']})" for p in c["parts"]) + ". ") if c["parts"] else "No replacement parts are usually needed. ") + c["tip"],
    ),
    (
        r"\b(tool|tools|need|what do i need|buy|butter knife|knife|substitute|instead of)\b",
        lambda c: "You'll need: " + "; ".join(c["tools"]) + (". Parts: " + "; ".join(f"{p['name']} ({p['cost']})" for p in c["parts"]) if c["parts"] else ".") + " Avoid using knives or improvised tools that can slip.",
    ),
    (r"\b(cost|price|how much|expensive|cheap)\b", lambda c: f"Doing it yourself: about {c['cost']['diy']}. Hiring someone: about {c['cost']['pro']}. Those are rough US ranges."),
    (r"\b(how long|time|minutes|hours)\b", lambda c: f"Plan on {c['time']}."),
    (r"\b(pro|professional|plumber|electrician|call|hire|someone)\b", lambda c: ("Call a professional if: " + "; ".join(c["call_pro_if"])) if c["call_pro_if"] else "This one rarely needs a professional."),
    (r"\b(safe|safety|danger|dangerous|risk|hurt|myself|diy|should i)\b", lambda c: f"{c['safety']['emoji']} {c['safety']['label']}. {c['safety']['blurb']} " + " ".join(c["safety"]["notes"])),
    (r"\b(step|steps)\b", lambda c: "Start here: " + c["steps"][0]),
    (r"\b(why|cause|causes|reason)\b", lambda c: "Most likely causes, in order: " + "; ".join(x["name"] for x in c["causes"][:3]) + "."),
]


def _template_ask(c: dict, question: str) -> str:
    q = question.lower()
    for rx, fn in _TEMPLATE_ANSWERS:
        if re.search(rx, q):
            return fn(c)
    return "Without an AI option turned on, I can only answer simple questions about this repair: tools, parts, cost, time, safety, when to call a pro, or the first step. The full steps are on the card above. " + c["tip"]


def ask(problem_id: str, description: str, question: str, history: list[dict], settings: Settings, photo_text: str = "", has_flags: bool = False) -> dict:
    c = card(problem_id, description=description, has_flags=has_flags)
    if not c:
        return {"answer": "I lost track of which repair we're talking about. Please run the check again.", "engine": "rules"}
    question = (question or "").strip()[:1000]
    if not question:
        return {"answer": "Type a question first.", "engine": "rules"}
    try:
        provider = _provider(settings)
    except Exception as exc:  # noqa: BLE001
        return {"answer": _template_ask(c, question), "engine": "rules", "error": f"AI settings error: {exc}"}
    if provider.name == "offline":
        return {"answer": _template_ask(c, question), "engine": "rules"}
    slim = {k: c[k] for k in ("title", "causes", "safety", "difficulty", "time", "cost", "tools", "parts", "steps", "call_pro_if", "tip")}
    convo = "".join(f"\n{h.get('role', 'user').upper()}: {str(h.get('text', ''))[:600]}" for h in (history or [])[-6:])
    user = (
        f"REPAIR CARD (JSON):\n{json.dumps(slim)}\n\n"
        f"WHAT THE PERSON ORIGINALLY SAID:\n{description[:1500]}\n\n"
        f"PHOTO DESCRIPTION (if any):\n{photo_text[:1000] or 'none'}\n\n"
        f"CONVERSATION SO FAR:{convo or ' (none)'}\n\n"
        f"QUESTION:\n{question}"
    )
    try:
        answer = provider.complete(ASK_SYSTEM, user, want_json=False)
    except ProviderError as exc:
        return {"answer": _template_ask(c, question), "engine": "rules", "error": str(exc)}
    answer = str(answer or "").strip()
    if not answer:
        answer = _template_ask(c, question)
    # Safety guard: if the card is pro/emergency (or model encourages hazardous DIY), prepend the warning.
    if (c["safety"]["level"] in ("pro", "emergency") and re.search(r"\b(you can|go ahead|easy|simple|yourself)\b", answer, re.I)) or _HAZARD_DIY_RE.search(answer):
        answer = f"⚠️ Remember: this one is marked \"{c['safety']['label']}\". {c['safety']['blurb']}\n\n" + answer
    return {"answer": answer[:3000], "engine": provider.name}
