"""AI engine providers.

Every provider exposes the same tiny interface so the rest of the app never
cares where the answer came from:

    provider.complete(system_prompt, user_prompt, want_json=False) -> str
    provider.check() -> (ok: bool, message: str)

Providers:
    OllamaProvider   - runs on the user's own computer (private, free)
    OpenAIProvider   - any OpenAI-compatible API (OpenAI, Groq, LM Studio, ...)
    GeminiProvider   - Google Gemini REST API
    OfflineProvider  - no AI at all; the rules engine in analysis/rules.py is used
"""

from __future__ import annotations

import json
import re

import base64

import httpx

from ..config import Settings

REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
LOCAL_TIMEOUT = httpx.Timeout(connect=5.0, read=900.0, write=30.0, pool=5.0)  # local CPU models can be slow


VISION_DEFAULTS = {"ollama": "moondream", "openai": "gpt-4o-mini", "gemini": "gemini-2.0-flash", "offline": ""}


class ProviderError(Exception):
    """Raised when an AI provider cannot answer. The message is user-friendly."""


class BaseProvider:
    name = "base"
    local = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.effective_model()

    def check(self) -> tuple[bool, str]:
        raise NotImplementedError

    def complete(self, system_prompt: str, user_prompt: str, want_json: bool = False, images: list[tuple[bytes, str]] | None = None) -> str:
        """`images` is an optional list of (raw_bytes, mime_type). Providers that can't see images raise ProviderError."""
        raise NotImplementedError

    def vision_model(self) -> str:
        """Model used when a photo is attached (cloud defaults already see images)."""
        return "" if self.name == "offline" else self.model


def model_matches(installed: list[str], target: str) -> bool:
    """Match an installed Ollama model name. If `target` specifies a `:tag`, require an exact tag match."""
    target = (target or "").strip()
    if not target:
        return False
    if ":" in target:
        return any(m == target for m in installed)
    return any(m == target or m.split(":")[0] == target for m in installed)


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------
class OllamaProvider(BaseProvider):
    name = "ollama"
    local = True

    def _host(self) -> str:
        return normalize_host(self.settings.ollama_host)

    def list_models(self) -> list[str]:
        try:
            r = httpx.get(f"{self._host()}/api/tags", timeout=3.0)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:  # noqa: BLE001 - includes InvalidURL, HTTPError, bad JSON
            return []

    def check(self) -> tuple[bool, str]:
        probe = detect_ollama(self.settings.ollama_host)
        if probe.get("error"):
            return False, probe["error"]
        if not probe["running"]:
            return False, "Ollama isn't running (or isn't installed). See Settings for the install steps, or start it with: ollama serve"
        models = probe["models"]
        if not models:
            return False, f"Ollama is running but has no models. Run: ollama pull {self.model}"
        if not model_matches(models, self.model):
            return False, f"Model '{self.model}' is not downloaded. Run: ollama pull {self.model}"
        return True, f"Ollama is ready with {self.model}"

    def vision_model(self) -> str:
        return (self.settings.vision_model or "").strip() or VISION_DEFAULTS["ollama"]

    def has_model(self, name: str) -> bool:
        return model_matches(self.list_models(), name)

    def complete(self, system_prompt: str, user_prompt: str, want_json: bool = False, images: list[tuple[bytes, str]] | None = None) -> str:
        try:
            return self._complete(system_prompt, user_prompt, want_json, temperature=0.1, images=images)
        except ProviderError as exc:
            if "repeat" in str(exc).lower():
                # Small models sometimes loop; one retry with more randomness usually clears it.
                return self._complete(system_prompt, user_prompt, want_json, temperature=0.5, images=images)
            raise

    def _complete(self, system_prompt: str, user_prompt: str, want_json: bool, temperature: float, images=None) -> str:
        model = self.model
        user_msg: dict = {"role": "user", "content": user_prompt}
        if images:
            model = self.vision_model()
            probe = detect_ollama(self.settings.ollama_host)
            if probe.get("error"):
                raise ProviderError(probe["error"])
            if not probe["running"]:
                raise ProviderError("Could not reach Ollama. Is it running? Try: ollama serve")
            if not model_matches(probe["models"], model):
                raise ProviderError(f"Looking at photos needs a vision model. Run: ollama pull {model}  (about 1.7 GB), or describe the problem in words instead.")
            user_msg["images"] = [base64.b64encode(b).decode("ascii") for b, _ in images]
        payload = {
            "model": model,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": 4096 if images else 8192, "repeat_penalty": 1.15, "num_predict": 1500 if images else 3000},
            "messages": [
                {"role": "system", "content": system_prompt},
                user_msg,
            ],
        }
        if want_json and not images:  # small vision models ignore/garble format=json; we parse leniently instead
            payload["format"] = "json"
        try:
            r = httpx.post(f"{self._host()}/api/chat", json=payload, timeout=LOCAL_TIMEOUT)
        except httpx.TimeoutException as exc:
            raise ProviderError("Ollama took too long to answer. Try a smaller model.") from exc
        except Exception as exc:  # noqa: BLE001 - connect errors, Ollama dying mid-request, bad URL
            raise ProviderError("Could not reach Ollama. Is it running? Try: ollama serve") from exc
        if r.status_code == 404:
            raise ProviderError(f"Model '{model}' is not downloaded. Run: ollama pull {model}")
        if r.status_code >= 400:
            if "repeat" in r.text.lower():
                raise ProviderError("The AI got stuck repeating itself (this happens with small models). Try again, or try a bigger model.")
            raise ProviderError(f"Ollama returned an error ({r.status_code}): {r.text[:200]}")
        try:
            content = r.json()["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content is not a string")
            return content
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Ollama sent back an answer the app could not read.") from exc


# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenAI, Groq, Together, LM Studio, llama.cpp server ...)
# ---------------------------------------------------------------------------
class OpenAIProvider(BaseProvider):
    name = "openai"
    local = False

    def __init__(self, settings: Settings):
        super().__init__(settings)
        # Local OpenAI-compatible servers keep data on-device.
        try:
            host = (httpx.URL(settings.openai_base_url).host if settings.openai_base_url.startswith("http") else "") or ""
        except Exception:  # noqa: BLE001
            host = ""
        self.local = host in ("127.0.0.1", "localhost", "::1")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.openai_api_key or 'none'}", "Content-Type": "application/json"}

    def check(self) -> tuple[bool, str]:
        if not self.settings.openai_api_key and not self.local:
            return False, "No API key saved. Paste your key in Settings."
        try:
            r = httpx.get(f"{self.settings.openai_base_url.rstrip('/')}/models", headers=self._headers(), timeout=8.0)
        except Exception:  # noqa: BLE001
            return False, "Could not reach the API server. Check the URL and your internet connection."
        if r.status_code == 401:
            return False, "The API key was rejected. Double-check it in Settings."
        if r.status_code >= 400:
            return False, f"The API server returned an error ({r.status_code})."
        return True, f"Connected. Using {self.model}"

    def complete(self, system_prompt: str, user_prompt: str, want_json: bool = False, images: list[tuple[bytes, str]] | None = None) -> str:
        content: object = user_prompt
        if images:
            content = [{"type": "text", "text": user_prompt}] + [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(b).decode('ascii')}", "detail": "low"}} for b, mime in images
            ]
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        if want_json:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = httpx.post(
                f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                headers=self._headers(),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("The API server took too long to answer. Try again in a moment.") from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("Could not reach the API server. Check the address and your internet connection.") from exc
        if r.status_code == 401:
            raise ProviderError("The API key was rejected. Double-check it in Settings.")
        if r.status_code == 429:
            raise ProviderError("The API says you are out of credits or sending too fast. Wait a minute and try again.")
        if r.status_code >= 400:
            # Some servers reject response_format; retry once without it.
            if want_json and "response_format" in payload:
                payload.pop("response_format")
                try:
                    r = httpx.post(
                        f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                        timeout=REQUEST_TIMEOUT,
                    )
                except Exception as exc:  # noqa: BLE001
                    raise ProviderError("Could not reach the API server. Check the address and your internet connection.") from exc
            if r.status_code >= 400:
                raise ProviderError(f"The API returned an error ({r.status_code}): {r.text[:200]}")
        try:
            out = r.json()["choices"][0]["message"]["content"]
            if not isinstance(out, str):
                raise TypeError("content is not a string")
            return out
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("The API sent back an answer the app could not read.") from exc


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------
class GeminiProvider(BaseProvider):
    name = "gemini"
    local = False
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self) -> dict:
        # Key goes in a header, never in the URL, so it can't end up in request logs.
        return {"x-goog-api-key": self.settings.gemini_api_key, "Content-Type": "application/json"}

    def check(self) -> tuple[bool, str]:
        if not self.settings.gemini_api_key:
            return False, "No Gemini API key saved. Paste your key in Settings."
        try:
            r = httpx.get(f"{self.BASE}/models", headers=self._headers(), timeout=8.0)
        except Exception:  # noqa: BLE001
            return False, "Could not reach Google. Check your internet connection."
        if r.status_code in (400, 401, 403):
            return False, "The Gemini API key was rejected. Double-check it in Settings."
        if r.status_code >= 400:
            return False, f"Gemini returned an error ({r.status_code})."
        return True, f"Connected. Using {self.model}"

    def complete(self, system_prompt: str, user_prompt: str, want_json: bool = False, images: list[tuple[bytes, str]] | None = None) -> str:
        parts: list[dict] = [{"text": user_prompt}]
        for b, mime in images or []:
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(b).decode("ascii")}})
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.1},
        }
        if want_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = httpx.post(
                f"{self.BASE}/models/{self.model}:generateContent",
                headers=self._headers(),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("Google took too long to answer. Try again in a moment.") from exc
        except Exception as exc:  # noqa: BLE001 - any transport/URL problem
            raise ProviderError("Could not reach Google. Check your internet connection.") from exc
        if r.status_code in (401, 403) or (r.status_code == 400 and ("api_key" in r.text.lower() or "api key" in r.text.lower() or "key" in r.text.lower())):
            raise ProviderError("The Gemini API key was rejected. Double-check it in Settings.")
        if r.status_code == 429:
            raise ProviderError("Gemini says you are sending too fast or out of quota. Wait a minute and try again.")
        if r.status_code >= 400:
            raise ProviderError(f"Gemini returned an error ({r.status_code}): {r.text[:200]}")
        try:
            out = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            if not isinstance(out, str):
                raise TypeError("content is not a string")
            return out
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("Gemini sent back an answer the app could not read.") from exc


# ---------------------------------------------------------------------------
# Offline (no AI)
# ---------------------------------------------------------------------------
class OfflineProvider(BaseProvider):
    name = "offline"
    local = True

    def check(self) -> tuple[bool, str]:
        return True, "No AI (basic mode): simple rules only. Nothing leaves this computer."

    def complete(self, system_prompt: str, user_prompt: str, want_json: bool = False, images: list[tuple[bytes, str]] | None = None) -> str:
        raise ProviderError("Basic mode has no AI model.")


PROVIDERS = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "offline": OfflineProvider,
}


def get_provider(settings: Settings) -> BaseProvider:
    cls = PROVIDERS.get(settings.provider, OfflineProvider)
    return cls(settings)


def normalize_host(host: str) -> str:
    """'127.0.0.1:11434' -> 'http://127.0.0.1:11434'. Raises ValueError with a friendly message if unusable."""
    host = (host or "").strip().rstrip("/")
    if not host:
        return "http://127.0.0.1:11434"
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    try:
        url = httpx.URL(host)
        if not url.host or not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9a-fA-F:]+\]", url.host):
            raise ValueError
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"'{host}' is not a valid address. It should look like http://127.0.0.1:11434") from exc
    return host


def detect_ollama(host: str = "http://127.0.0.1:11434") -> dict:
    """One quick probe. Used by the first-run screen, Settings, and the Ollama provider."""
    try:
        base = normalize_host(host)
    except ValueError as exc:
        return {"running": False, "models": [], "error": str(exc)}
    try:
        r = httpx.get(f"{base}/api/tags", timeout=2.0)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return {"running": True, "models": models}
    except Exception:  # noqa: BLE001
        return {"running": False, "models": []}


# ---------------------------------------------------------------------------
# JSON repair: small local models sometimes wrap JSON in prose or code fences.
# ---------------------------------------------------------------------------
def extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    # strip ```json fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # find the outermost {...}
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # last resort: remove trailing commas
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
