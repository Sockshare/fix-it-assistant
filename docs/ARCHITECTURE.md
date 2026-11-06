# Architecture

For people who want to change the code. Plain Python, plain JavaScript, no build step.

```
app.py                      launcher: checks Python/deps, picks a free port, opens the browser
fix_it_assistant/
  server.py                 FastAPI app, 127.0.0.1 only; all endpoints; the repair notebook store
  knowledge.py              the no-AI brain: KB aggregation, schema validation, RED_FLAGS, match(), rank_causes(), card()
  kb/                       the repair guide itself — one list of dicts per area
    plumbing.py  electrical.py  appliances.py  home.py (HVAC, doors, walls)  outdoor.py (yard, garage, car, bike)
  ai.py                     optional AI layer: describe_photo(), explain(), ask() — each with a rules fallback
  config.py                 Settings dataclass; ~/.fix_it_assistant/config.json; env overrides
  llm/providers.py          Ollama / OpenAI-compatible / Gemini / Offline, all with an `images=` path
  static/                   index.html, app.js, app.css — three screens + four modals
tests/
  unit/                     KB integrity, matcher accuracy, red flags, ranking, AI guards (fake provider)
  smoke/                    every endpoint, bad input, privacy (socket guard, CSRF, file modes)
  ui/                       Playwright click-through at three widths, zero console errors
scripts/                    selfcheck.py, fresh_install_test.sh, readability_check.py
```

## Design rules

**Rules outrank the model.** The knowledge base decides the safety level, the "call a pro" triggers, and which problems exist. `knowledge.match()` produces the candidate list; `ai.explain()` may only choose from it (anything else is coerced back to the rules' top pick or `None`). `ai.ask()` is given exactly one card and this repair's own description/chat. Both functions post-filter the model's text: if it calls a `pro`/`emergency` card "easy", "simple" or "yourself", the text is replaced or prefixed with the card's warning.

**Emergencies are regex, not AI.** `RED_FLAGS` in `knowledge.py` runs first on the raw text and the result is returned regardless of matches. Patterns are written to avoid crying wolf ("gas stove won't light" is not a gas leak; the tests check both directions).

**Honest confidence.** The matcher needs an *anchor* (the object) for full credit; symptoms alone are scaled down. An area hint only breaks ties — it can't create a match. Confidence is relative to the best score and the UI says "I'm not sure" when it's low.

**Photos are inputs, not storage.** The image bytes are validated (type, magic bytes, ≤ 8 MB, streamed), passed to the provider in memory, and dropped. Only the model's text description is kept.

**No-AI fallbacks everywhere.** Every AI function has a `_template_*` twin so the app behaves identically — just less chatty — when offline, when the provider errors, or when the model returns junk.

## The knowledge base schema

Each problem is a dict (documented at the top of `knowledge.py`). The key fields:

- `anchors` — words that identify the *thing*. At least one must appear for a confident match.
- `keywords` — `{phrase: weight}` symptoms. Multi-word phrases are matched as substrings; single words get light stemming.
- `questions` / `causes[].answers` — follow-ups and which answer points at which cause. `rank_causes()` moves agreeing causes up and disagreeing ones down.
- `safety.level` — `diy | careful | pro | emergency`. Tests enforce that electrical entries (except a few low-voltage ones) are at least `careful`, and that `careful`/`pro` entries have `call_pro_if`.

`validate()` checks the whole thing and runs in the unit tests, so a typo in a question id fails CI rather than confusing a user.

## Adding a problem

1. Copy an entry in the right `kb/*.py` file. Give it a unique `id`.
2. Fill in anchors, keywords (think: how would a non-expert *say* this?), causes, safety, steps.
3. Add a line to `test_matcher_top_pick` in `tests/unit/test_core.py` with a realistic sentence.
4. `pytest tests/unit` — fix any schema or false-positive failures (new keywords can steal matches from old entries; the parametrized cases catch that).

## Adding a provider

Subclass `BaseProvider` in `llm/providers.py`, implement `check()` and `complete(system, user, want_json=False, images=None)`, raise `ProviderError` with a *user-friendly* message for every failure path, and register it in `PROVIDERS`. If it can see images, handle `images` (a list of `(bytes, mime)`); if not, raise `ProviderError` explaining that.

## Server notes

- `TrustedHostMiddleware` + an `Origin`/`Sec-Fetch-Site` check block DNS-rebinding and cross-site writes.
- Repair files are written atomically (`tmp` + `os.replace`) with mode `600` under a lock; ids are validated as 12 hex chars before touching the filesystem.
- With `save_history` off, repairs live in `_MEM` only and are flagged `unsaved` in the list.
- `/api/status` is network-silent unless `?probe=1` (used by Settings/first-run only).

## Running the gates

```bash
pytest                                  # G1 unit, G2 smoke, G4 UI, G6 privacy
bash scripts/fresh_install_test.sh      # G3
python scripts/readability_check.py     # G5
```

Reviewer reports (G7/G8) and the real-model run (G9) are recorded in `QUALITY.md`.
