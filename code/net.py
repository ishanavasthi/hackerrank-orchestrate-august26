"""Shared HTTP retry for the LLM providers.

Added after a single transient `HTTP 503` from Anthropic killed a full
110-message gate run partway through. Provider APIs return 429/5xx routinely
under load; without backoff, one blip discards every uncached call made so far.

Retries are safe here specifically because every call site is idempotent and
cached by message_id — a retried request either lands and is cached, or fails
and is retried again. Retrying does not affect determinism.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

# On Python 3.10+ socket.timeout IS TimeoutError; on 3.9 (what this repo runs)
# it is a distinct class, so catching only TimeoutError misses read timeouts
# entirely. That gap killed the first full 110-message run.
_TRANSIENT_EXC = (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)

# Transient by nature: rate limiting, and the 5xx family. A 4xx other than 429
# means the request itself is wrong and retrying just burns quota.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

DEFAULT_ATTEMPTS = 5
BASE_DELAY_SECONDS = 2.0
# Large NIM models can take well over a minute on a long prompt; 120s produced
# read timeouts mid-run.
DEFAULT_TIMEOUT_SECONDS = 300


def post_json(
    url: str,
    payload: dict,
    headers: dict,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_ATTEMPTS,
    verbose: bool = True,
) -> dict:
    """POST JSON and return the decoded response, retrying transient failures.

    Raises RuntimeError with the response body on a non-retryable status, or
    after the final attempt.
    """
    body = json.dumps(payload).encode()
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            if exc.code not in RETRYABLE_STATUS or attempt == attempts:
                raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
            last_error = exc
            # Honour Retry-After when the server sends one.
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = float(retry_after) if retry_after else BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            except (TypeError, ValueError):
                delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))

        except _TRANSIENT_EXC as exc:
            if attempt == attempts:
                raise RuntimeError(f"connection error from {url}: {exc}") from exc
            last_error = exc
            delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))

        if verbose:
            print(f"    transient failure ({last_error}); retrying in {delay:.0f}s "
                  f"[attempt {attempt + 1}/{attempts}]", flush=True)
        time.sleep(delay)

    raise RuntimeError(f"exhausted {attempts} attempts against {url}: {last_error}")
