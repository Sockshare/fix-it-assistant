# Fix-It Assistant — Quality & Verification Report

All 9 Quality Gates (G1–G9) were executed and verified prior to release.

## Gate Summary

| Gate | Requirement | Status | Evidence |
| :--- | :--- | :---: | :--- |
| **G1** | Automated unit & integration tests | **PASS** | `pytest` passes all unit, integration, and regression tests (`tests/unit/`, `tests/integration/`) with network sockets blocked. |
| **G2** | Self-check & API smoke verification | **PASS** | `python scripts/selfcheck.py` passes all 9 checks (knowledge base schema, emergency red-flag rules, matcher, offline Q&A, settings/key masking, HTTP endpoints, security headers). |
| **G3** | Readability & plain-language audit | **PASS** | `python scripts/readability_check.py` verifies all 47 repair cards and user docs stay below grade 8.0 Flesch-Kincaid reading level with zero banned jargon words. |
| **G4** | Browser E2E & zero console errors | **PASS** | Headless Chromium (Playwright) exercised setup, diagnosis, emergency flags, clarifying questions, grounded chat, browse modal, notebook, and settings across desktop (`1360x900`) and mobile (`375x812`) with `0` console/page errors. |
| **G5** | Real screenshots embedded in docs | **PASS** | 6 screenshots in `docs/screenshots/` (`01_first_run.png` – `06_mobile.png`) verified and linked in `README.md` and `docs/USER_GUIDE.md`. |
| **G6** | Zero-key offline mode | **PASS** | Full diagnosis, emergency red-flag override, cause re-ranking, and template Q&A work with `provider="offline"` and zero network access. |
| **G7** | Secret & personal-path scan | **PASS** | `0` matches across tracked files for API keys, tokens, or local user paths. |
| **G8** | Independent Code/Security & Novice UX reviews | **PASS** | All P0 and P1 findings from both independent reviews resolved and covered by `tests/unit/test_review_fixes.py`. |
| **G9** | Real Vision + LLM end-to-end verification | **PASS** | Verified live against local Ollama (`moondream` vision + `llama3.2:3b` text): described a real toilet tank photo, diagnosed `toilet_running`, answered 3 follow-up questions in plain English, enforced the `"Call a professional"` safety guard on `microwave_dead`, and triggered the `gas_smell` emergency banner on gas leak input. |

## Key Fixes Applied in G8

1. **Emergency Red-Flag Precision & Recall (`fix_it_assistant/knowledge.py`)**:
   - Eliminated false-positive `co_alarm` emergency banner when a user reports a routine low-battery chirp on a CO/smoke detector (`"carbon monoxide detector chirps every minute"`).
   - Expanded `structural`, `electrical_fire`, `water_electric`, `gas_smell`, and `co_alarm` patterns to match natural English word order (`"wide crack in the foundation"`, `"basement wall is bowing"`, `"ceiling light is sparking"`, `"hissing gas pipe"`).
   - Escalated `card()` safety banner to `🔴 Danger — stop and act now` whenever emergency red flags are present, and to `🟠 Call a professional` when a broken garage torsion spring or cable is mentioned.
2. **Matcher & Stemmer Accuracy (`fix_it_assistant/knowledge.py`, `fix_it_assistant/kb/`)**:
   - Fixed `_stem()` on silent-`e` plurals (`tires` → `tire`, `pipes` → `pipe`, `wires` → `wire`, `hinges` → `hinge`) and 4-letter plurals (`cars` → `car`, `tubs` → `tub`).
   - Added common household typo normalization (`tolet`, `runing`, `drian`, `refrigerater`, `disposel`, etc.), OBD-II trouble code matching (`P0420` → `p0`), and an out-of-scope consumer electronics filter (`laptop`, `iphone`, `wifi`, etc.).
3. **LLM Resilience & Safety Post-Filter (`fix_it_assistant/ai.py`, `fix_it_assistant/llm/providers.py`)**:
   - Fixed `ai.explain()` so malformed model JSON or omitted `"pick"` fields fall back to the top rule-matched candidate instead of dropping the repair card.
   - Extended the safety post-filter in `explain()` and `ask()` to block DIY encouragement on hazardous components (torsion springs, capacitors, magnetrons, refrigerant, gas valves).
   - Fixed `OllamaProvider` tag-aware model matching (`llama3.2:3b` vs `llama3.2:1b`) and ensured unreachable Ollama servers report `"Try: ollama serve"` when a photo is attached.
4. **Privacy, Concurrency & UX Polish (`fix_it_assistant/server.py`, `fix_it_assistant/static/`)**:
   - Configured Starlette `UploadFile.spool_max_size` above `MAX_PHOTO_BYTES` (8 MB) so uploaded photos never spool to `/tmp` on disk, and enforced strict MIME-to-magic-byte matching.
   - Offloaded synchronous `/api/diagnose` work to Starlette's threadpool, prevented concurrent `/api/ask` completions from overwriting status/answer updates, and prevented guide browsing (`openFromBrowse`) from invoking the LLM or polluting saved repairs.
   - Fixed `.steps` CSS rules so repair steps render as a full-width numbered list alongside checkboxes.
