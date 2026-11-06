"""FastAPI server for Fix-It Assistant. Binds to 127.0.0.1 only."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.formparsers import MultiPartParser
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, ai, knowledge
from .config import SettingsError, data_dir, load_settings, update_settings, validate_changes
from .llm import detect_ollama, get_provider
from .llm.providers import VISION_DEFAULTS, model_matches

# Keep uploaded photos (up to 8 MB) in RAM instead of spooling temporary files to /tmp.
StarletteUploadFile.spool_max_size = ai.MAX_PHOTO_BYTES + 1024
MultiPartParser.max_file_size = ai.MAX_PHOTO_BYTES + 1024

log = logging.getLogger("fix_it_assistant")
ROOT = Path(__file__).parent
STATIC = ROOT / "static"
MAX_DESC = 4000
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

app = FastAPI(title="Fix-It Assistant", version=__version__, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def block_cross_site_writes(request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin", "")
        sfs = request.headers.get("sec-fetch-site", "")
        host = (urlparse(origin).hostname or "") if origin else ""
        if (origin and host not in ("127.0.0.1", "localhost", "::1")) or (sfs and sfs not in ("same-origin", "none")):
            return JSONResponse(status_code=403, content={"detail": "Blocked a request from another website."})
    return await call_next(request)


def _provider_status(s, live: bool = True) -> dict:
    """live=False answers from saved settings alone (no network, no key sent anywhere)."""
    try:
        provider = get_provider(s)
    except Exception as exc:  # noqa: BLE001
        log.warning("provider init failed: %s", exc)
        return {"name": s.provider, "model": s.effective_model(), "local": True, "ok": False, "message": "The saved AI settings are invalid. Open Settings and fix the address, or pick No AI."}
    if not live and provider.name in ("openai", "gemini"):
        has_key = bool(s.openai_api_key if provider.name == "openai" else s.gemini_api_key) or provider.local
        return {"name": provider.name, "model": provider.model, "local": provider.local, "ok": has_key, "message": "Key saved. Click Test in Settings to verify it.", "vision_model": provider.vision_model()} if has_key else {"name": provider.name, "model": provider.model, "local": provider.local, "ok": False, "message": "No API key saved. Paste your key in Settings.", "vision_model": provider.vision_model()}
    try:
        ok, message = provider.check()
    except Exception as exc:  # noqa: BLE001
        log.warning("provider check failed: %s", exc)
        ok, message = False, "Could not check the AI option. Open Settings and click Test."
    return {"name": provider.name, "model": provider.model, "local": provider.local, "ok": ok, "message": message, "vision_model": provider.vision_model()}


# ---------------------------------------------------------------------------
# Repair notebook: one JSON per repair in ~/.fix_it_assistant/repairs/
# ---------------------------------------------------------------------------
def _repairs_dir() -> Path:
    d = data_dir() / "repairs"
    d.mkdir(exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


_LOCK = threading.Lock()
_MEM: dict[str, dict] = {}
_ID = re.compile(r"^[0-9a-f]{12}$")


def _valid_id(rid: str) -> bool:
    return bool(_ID.fullmatch(rid or ""))


def _save(doc: dict) -> None:
    p = _repairs_dir() / f"{doc['id']}.json"
    if not load_settings().save_history:
        _MEM[doc["id"]] = doc
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
        return
    _MEM.pop(doc["id"], None)
    tmp = p.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, p)


def _load(rid: str) -> dict | None:
    if not _valid_id(rid):
        return None
    if rid in _MEM:
        return _MEM[rid]
    p = _repairs_dir() / f"{rid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _summary(d: dict, unsaved: bool = False) -> dict:
    out = {"id": d["id"], "title": d.get("title") or "Untitled", "area": d.get("area", ""), "status": d.get("status", "open"), "updated": d.get("updated", 0), "safety_level": d.get("safety_level", "diy")}
    if unsaved:
        out["unsaved"] = True
    return out


def _list() -> list[dict]:
    with _LOCK:
        by_id: dict[str, dict] = {rid: _summary(d, unsaved=True) for rid, d in _MEM.items()}
        for p in _repairs_dir().glob("*.json"):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d["id"] not in by_id:
                    by_id[d["id"]] = _summary(d)
            except (json.JSONDecodeError, OSError, KeyError, TypeError):
                continue
    return sorted(by_id.values(), key=lambda a: -a["updated"])


# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


@app.get("/api/status")
def status(probe: int = 0):
    s = load_settings()
    ollama = detect_ollama(s.ollama_host) if (s.provider == "ollama" or probe) else {"running": False, "models": [], "skipped": True}
    if s.provider == "ollama":
        provider = get_provider(s)
        vm = provider.vision_model()
        if ollama.get("error"):
            pstat = {"ok": False, "message": ollama["error"]}
        elif not ollama["running"]:
            pstat = {"ok": False, "message": "Ollama isn't running (or isn't installed). See Settings for the install steps, or start it with: ollama serve"}
        elif not ollama["models"]:
            pstat = {"ok": False, "message": f"Ollama is running but has no models. Run: ollama pull {provider.model}"}
        elif not model_matches(ollama["models"], provider.model):
            pstat = {"ok": False, "message": f"Model '{provider.model}' is not downloaded. Run: ollama pull {provider.model}"}
        else:
            pstat = {"ok": True, "message": f"Ollama is ready with {provider.model}"}
        has_vision = model_matches(ollama.get("models", []), vm)
        pstat.update({"name": provider.name, "model": provider.model, "local": True, "vision_model": vm, "vision_ready": has_vision})
    else:
        pstat = _provider_status(s, live=bool(probe))
        pstat["vision_ready"] = pstat.get("ok", False) and s.provider in ("openai", "gemini")
    return {
        "settings": s.public_dict(), "provider": pstat, "ollama": ollama, "vision_defaults": VISION_DEFAULTS,
        "repairs": _list(), "data_dir": str(data_dir()), "areas": knowledge.AREAS, "problem_count": len(knowledge.PROBLEMS),
        "env_overrides": sorted(k for k in ("FIXIT_PROVIDER", "FIXIT_MODEL", "FIXIT_VISION_MODEL", "OLLAMA_HOST", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL") if os.environ.get(k)),
    }


class SettingsIn(BaseModel):
    provider: str | None = None
    model: str | None = None
    vision_model: str | None = None
    ollama_host: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    gemini_api_key: str | None = None
    save_history: bool | None = None
    setup_complete: bool | None = None


@app.post("/api/settings")
def set_settings(body: SettingsIn):
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        s = update_settings(changes)
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"settings": s.public_dict(), "provider": _provider_status(s)}


@app.post("/api/test-connection")
def test_connection(body: SettingsIn):
    s = load_settings()
    try:
        changes = validate_changes({k: v for k, v in body.model_dump().items() if v is not None})
    except SettingsError as exc:
        return {"ok": False, "message": str(exc), "model": ""}
    for k, v in changes.items():
        if hasattr(s, k):
            if k in ("openai_api_key", "gemini_api_key") and "*" in str(v):
                continue
            setattr(s, k, v)
    st = _provider_status(s)
    return {"ok": st["ok"], "message": st["message"], "model": st["model"]}


# ---------------------------------------------------------------------------
# Knowledge base browsing
# ---------------------------------------------------------------------------
@app.get("/api/browse")
def browse():
    return {"areas": knowledge.browse()}


@app.get("/api/problem/{problem_id}")
def problem(problem_id: str):
    c = knowledge.card(problem_id)
    if not c:
        raise HTTPException(404, "No such problem in the guide.")
    return c


# ---------------------------------------------------------------------------
# Diagnose
# ---------------------------------------------------------------------------
async def _read_photo(photo: UploadFile | None) -> tuple[bytes, str] | None:
    if photo is None or not photo.filename:
        return None
    mime = (photo.content_type or "").lower()
    if mime not in IMAGE_TYPES:
        raise HTTPException(400, "Photos must be JPG, PNG or WebP. HEIC from iPhones: take a screenshot of it, or change the camera setting to 'Most compatible'.")
    buf = bytearray()
    while True:
        chunk = await photo.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > ai.MAX_PHOTO_BYTES:
            raise HTTPException(400, "That photo is bigger than 8 MB. Most phones can share a smaller size, or take a screenshot of it.")
    if len(buf) == 0:
        raise HTTPException(400, "That photo file looks empty.")
    if len(buf) < 64:
        raise HTTPException(400, "That file doesn't look like a real photo.")
    head = bytes(buf[:12])
    ok_magic = (
        (mime == "image/jpeg" and head.startswith(b"\xff\xd8\xff"))
        or (mime == "image/png" and head.startswith(b"\x89PNG\r\n\x1a\n"))
        or (mime == "image/webp" and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )
    if not ok_magic:
        raise HTTPException(400, "That file doesn't look like a real photo.")
    return bytes(buf), mime


def _run_diagnose_sync(description: str, area: str, repair_id: str, img: tuple[bytes, str] | None) -> dict:
    s = load_settings()
    photo_info = {"text": "", "engine": ""}
    if img:
        photo_info = ai.describe_photo([img], s)
    result = knowledge.match(description, area_hint=area, photo_text=photo_info.get("text", ""))
    has_flags = bool(result["flags"])
    expl = ai.explain(description, photo_info.get("text", ""), result["candidates"], s, has_flags=has_flags)
    pick = expl.get("pick")
    # The model may re-order, but only among what the rules found.
    if pick and result["candidates"] and result["candidates"][0]["id"] != pick:
        result["candidates"].sort(key=lambda c: 0 if c["id"] == pick else 1)
    with _LOCK:
        doc = _load(repair_id) if repair_id else None
        card = knowledge.card(pick, (doc or {}).get("answers"), description=description, has_flags=has_flags) if pick else None
        now = time.time()
        if doc is None:
            doc = {"id": uuid.uuid4().hex[:12], "created": now, "status": "open", "answers": {}, "chat": []}
        doc.update({
            "updated": now, "description": description, "area_hint": area, "photo_text": photo_info.get("text", ""),
            "flags": result["flags"], "candidates": result["candidates"], "problem_id": pick,
            "title": result["flags"][0]["title"] if result["flags"] else (card["title"] if card else "Unmatched problem"),
            "area": card["area"] if card else area, "safety_level": "emergency" if result["flags"] else (card["safety"]["level"] if card else "diy"),
            "explanation": expl.get("why", ""), "engine": expl.get("engine", "rules"),
        })
        _save(doc)
    out = {
        "id": doc["id"], "flags": result["flags"], "candidates": result["candidates"], "confident": result["confident"],
        "explanation": expl.get("why", ""), "suggested_question": expl.get("question", ""), "engine": expl.get("engine", "rules"),
        "photo": {"used": bool(img), "text": photo_info.get("text", ""), "engine": photo_info.get("engine", ""), "error": photo_info.get("error", "")},
        "card": card,
        "saved": bool(s.save_history),
    }
    if expl.get("error"):
        out["error"] = expl["error"]
    return out


@app.post("/api/diagnose")
async def diagnose(description: str = Form(""), area: str = Form(""), repair_id: str = Form(""), photo: UploadFile | None = File(None)):
    description = (description or "").strip()[:MAX_DESC]
    area = area if area in knowledge.AREAS else ""
    img = await _read_photo(photo)
    if len(description) < 8 and not img:
        raise HTTPException(400, "Tell me a bit more: what is the thing, and what is it doing? (Or add a photo.)")
    return await run_in_threadpool(_run_diagnose_sync, description, area, repair_id, img)


class AnswersIn(BaseModel):
    repair_id: str = ""
    problem_id: str = ""
    answers: dict = {}


@app.post("/api/answers")
def answers(body: AnswersIn):
    with _LOCK:
        doc = _load(body.repair_id) if body.repair_id else None
        pid = body.problem_id or (doc.get("problem_id") if doc else "")
        if pid not in knowledge.BY_ID:
            if not doc:
                raise HTTPException(404, "That check wasn't found. Run it again.")
            raise HTTPException(400, "Pick a problem first.")
        now = time.time()
        if not doc:
            c0 = knowledge.BY_ID[pid]
            doc = {
                "id": uuid.uuid4().hex[:12], "created": now, "updated": now, "status": "open",
                "description": c0["title"], "area_hint": c0["area"], "photo_text": "",
                "flags": [], "candidates": [{"id": pid, "title": c0["title"], "area": c0["area"], "confidence": "high", "matched": []}],
                "problem_id": pid, "answers": {}, "chat": [],
            }
        clean = {str(k)[:40]: str(v)[:120] for k, v in (body.answers or {}).items()}
        doc["answers"] = clean
        doc["problem_id"] = pid
        c = knowledge.card(pid, clean, description=doc.get("description", ""), has_flags=bool(doc.get("flags")))
        doc["title"], doc["area"], doc["safety_level"] = c["title"], c["area"], ("emergency" if doc.get("flags") else c["safety"]["level"])
        doc["updated"] = now
        _save(doc)
    return {"card": c, "id": doc["id"]}


class AskIn(BaseModel):
    repair_id: str = ""
    problem_id: str = ""
    question: str


@app.post("/api/ask")
def ask(body: AskIn):
    with _LOCK:
        doc = _load(body.repair_id) if body.repair_id else None
        pid = body.problem_id or (doc.get("problem_id") if doc else "")
        if not doc and pid in knowledge.BY_ID:
            now = time.time()
            c0 = knowledge.BY_ID[pid]
            doc = {
                "id": uuid.uuid4().hex[:12], "created": now, "updated": now, "status": "open",
                "description": c0["title"], "area_hint": c0["area"], "photo_text": "",
                "flags": [], "candidates": [{"id": pid, "title": c0["title"], "area": c0["area"], "confidence": "high", "matched": []}],
                "problem_id": pid, "title": c0["title"], "area": c0["area"], "safety_level": c0["safety"]["level"],
                "answers": {}, "chat": [],
            }
            _save(doc)
        if not doc:
            raise HTTPException(404, "That check wasn't found. Run it again.")
        if not pid:
            raise HTTPException(400, "Pick a problem from the matches first, then ask about it.")
        rid = doc["id"]
        desc = doc.get("description", "")
        photo_text = doc.get("photo_text", "")
        has_flags = bool(doc.get("flags"))
        history = list(doc.get("chat", []))
    q = (body.question or "").strip()
    if not q:
        raise HTTPException(400, "Type a question first.")
    out = ai.ask(pid, desc, q, history, load_settings(), photo_text=photo_text, has_flags=has_flags)
    with _LOCK:
        fresh = _load(rid)
        if fresh is not None:
            fresh.setdefault("chat", []).extend([{"role": "user", "text": q[:1000], "when": time.time()}, {"role": "assistant", "text": out["answer"], "when": time.time()}])
            fresh["chat"] = fresh["chat"][-40:]
            fresh["updated"] = time.time()
            _save(fresh)
    return {**out, "id": rid}


# ---------------------------------------------------------------------------
# Notebook
# ---------------------------------------------------------------------------
@app.get("/api/repairs")
def repairs():
    return _list()


@app.get("/api/repairs/{rid}")
def repair(rid: str):
    doc = _load(rid)
    if not doc:
        raise HTTPException(404, "Not found.")
    pid = doc.get("problem_id")
    return {**doc, "card": knowledge.card(pid, doc.get("answers"), description=doc.get("description", ""), has_flags=bool(doc.get("flags"))) if pid else None}


class StatusIn(BaseModel):
    status: str
    note: str | None = None
    problem_id: str | None = None


@app.post("/api/repairs/{rid}/status")
def repair_status(rid: str, body: StatusIn):
    if body.status not in ("open", "fixed", "called_pro", "gave_up"):
        raise HTTPException(400, "Unknown status.")
    with _LOCK:
        doc = _load(rid)
        if not doc and body.problem_id and body.problem_id in knowledge.BY_ID:
            now = time.time()
            c0 = knowledge.BY_ID[body.problem_id]
            doc = {
                "id": uuid.uuid4().hex[:12], "created": now, "updated": now, "status": "open",
                "description": c0["title"], "area_hint": c0["area"], "photo_text": "",
                "flags": [], "candidates": [{"id": body.problem_id, "title": c0["title"], "area": c0["area"], "confidence": "high", "matched": []}],
                "problem_id": body.problem_id, "title": c0["title"], "area": c0["area"], "safety_level": c0["safety"]["level"],
                "answers": {}, "chat": [],
            }
        if not doc:
            raise HTTPException(404, "Not found.")
        doc["status"] = body.status
        if body.note is not None:
            doc["note"] = body.note.strip()[:2000]
        doc["updated"] = time.time()
        _save(doc)
    return {"ok": True, "id": doc["id"]}


@app.delete("/api/repairs/{rid}")
def delete_repair(rid: str):
    if not _valid_id(rid):
        raise HTTPException(404, "Not found.")
    with _LOCK:
        _MEM.pop(rid, None)
        p = _repairs_dir() / f"{rid}.json"
        if p.exists():
            p.unlink()
    return {"ok": True}


@app.delete("/api/repairs")
def delete_all():
    with _LOCK:
        _MEM.clear()
        n = 0
        for p in _repairs_dir().glob("*.json"):
            p.unlink()
            n += 1
    return {"ok": True, "deleted": n}
