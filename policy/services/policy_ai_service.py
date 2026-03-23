"""
Standalone AI client for policy analysis.

Fully decoupled from the clause-analyzer provider pool.
Driven entirely by three environment variables — no code changes needed to
switch provider, model, or key:

  POLICY_AI_API_KEY   — API key for any OpenAI-compatible provider
  POLICY_AI_MODEL     — model ID (e.g. gpt-4o, llama-3.3-70b-versatile)
  POLICY_AI_BASE_URL  — provider base URL (e.g. https://api.openai.com/v1)

Falls back to sensible defaults so the service degrades gracefully during
local development when env vars are absent.
"""
import json
import logging
import threading

from django.conf import settings
from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError

logger = logging.getLogger(__name__)


class PolicyRateLimitError(Exception):
    """Raised when the AI provider returns a 429 rate-limit response."""
    pass


# ── Client — created once, reused for all calls ────────────────────────────────

_client_lock = threading.Lock()
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OpenAI(
                    api_key=getattr(settings, "POLICY_AI_API_KEY", "no-key"),
                    base_url=getattr(settings, "POLICY_AI_BASE_URL", "https://api.openai.com/v1"),
                    max_retries=0,
                    timeout=120.0,
                )
    return _client


def _get_model() -> str:
    return getattr(settings, "POLICY_AI_MODEL", "gpt-4o")


# ── AI call ────────────────────────────────────────────────────────────────────

def call_ai(
    messages: list,
    *,
    temperature: float = 0.1,
    seed: int | None = 42,
) -> str:
    """
    Send *messages* to the configured provider and return the response text.

    Raises RuntimeError on all unrecoverable failures so the caller can decide
    how to handle them (policy_service wraps this in its own try/except).
    """
    client = _get_client()
    model = _get_model()

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            seed=seed,
        )
        if not resp.choices:
            raise RuntimeError("Policy AI returned empty choices (content filter or provider error)")
        return (resp.choices[0].message.content or "").strip()
    except RateLimitError as exc:
        raise PolicyRateLimitError(f"Policy AI rate-limited: {exc}") from exc
    except APIStatusError as exc:
        raise RuntimeError(f"Policy AI HTTP {exc.status_code}: {exc.message}") from exc
    except APIConnectionError as exc:
        raise RuntimeError(f"Policy AI connection error: {exc}") from exc


# ── JSON repair ────────────────────────────────────────────────────────────────

def _escape_control_chars_in_strings(text: str) -> str:
    """Escape literal control characters that appear inside JSON string values."""
    _CTRL = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}
    result: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                result.append(ch)
                if i + 1 < len(text):
                    result.append(text[i + 1])
                    i += 2
                    continue
            elif ch == '"':
                in_string = False
                result.append(ch)
            elif ord(ch) < 0x20:
                result.append(_CTRL.get(ch, f"\\u{ord(ch):04x}"))
            else:
                result.append(ch)
        else:
            if ch == '"':
                in_string = True
            result.append(ch)
        i += 1
    return "".join(result)


def safe_json_parse(text: str) -> dict:
    """
    Parse *text* as JSON.  Attempts increasingly aggressive repair
    strategies before giving up, so truncated/dirty AI responses don't hard-fail.
    """
    # Strategy 1: direct parse (happy path)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 1b: escape literal control characters inside string values
    try:
        return json.loads(_escape_control_chars_in_strings(text))
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract the outermost {...} block
    start = text.find("{")
    if start != -1:
        depth = 0
        end = -1
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

    # Strategy 3: force-close all open structures (handles mid-string truncation)
    try:
        partial = text[start:] if start != -1 else text
        partial = partial.rstrip().rstrip(",")

        # Count unescaped quotes to detect mid-string truncation.
        # Odd count means the response was cut off inside a string value.
        quote_count = 0
        i = 0
        while i < len(partial):
            if partial[i] == "\\":
                i += 2
                continue
            if partial[i] == '"':
                quote_count += 1
            i += 1
        if quote_count % 2 == 1:
            partial += '"'  # close the dangling string

        partial = partial.rstrip(",")  # strip any trailing comma left after string close

        # Build a stack of unclosed openers by scanning the (now valid-string)
        # partial JSON, so we can close them in correct reverse order.
        stack: list[str] = []
        in_str = False
        j = 0
        while j < len(partial):
            ch = partial[j]
            if in_str:
                if ch == "\\":
                    j += 2
                    continue
                if ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in "{[":
                    stack.append(ch)
                elif ch == "}" and stack and stack[-1] == "{":
                    stack.pop()
                elif ch == "]" and stack and stack[-1] == "[":
                    stack.pop()
            j += 1

        # Close open structures in reverse order (innermost first)
        for opener in reversed(stack):
            partial += "}" if opener == "{" else "]"

        return json.loads(partial)
    except (json.JSONDecodeError, Exception):
        pass

    raise json.JSONDecodeError("Cannot repair policy AI response", text, len(text))
