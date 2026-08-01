"""M1 reasoning layer: one routing call per message.

Owned files (see contracts.py file-ownership header): code/router.py,
code/prompts.py. This module does NOT implement the safety gate (M2) or the
personalization stage (M3) — the stub heuristic below is deliberately crude,
and the LLM providers make one flat routing call with no separate risk pass.

Entry points:
    route(ctx, provider)      -> Decision
    route_all(contexts, provider) -> list[Decision]

Providers:
    stub      offline, deterministic heuristic. No network, no API key.
              Always returns a valid Decision, even for empty message_text
              or missing media extraction. This is the M1 gate and the
              permanent fallback that guarantees a submittable output.csv.
    anthropic claude-haiku-4-5 via the Anthropic Messages API.
    nvidia    OpenAI-compatible chat completions against NVIDIA NIM.

Both live providers: temperature 0, cached to code/cache/routing/<provider>/
<message_id>.json and reused on rerun (determinism is a locked decision, see
DECISIONS.md). A missing API key raises rather than silently using the stub,
so a broken key can never look like a working run.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.error
import urllib.request
from typing import Optional

try:
    from contracts import (
        ACTIONS,
        DEFAULT_PROVIDER,
        Decision,
        MESSAGE_TYPES,
        MessageContext,
        NO_EVIDENCE,
    )
    from prompts import SYSTEM_PROMPT, build_user_prompt
except ImportError:  # running as part of the `code` package (e.g. `python -m code.main`)
    from code.contracts import (
        ACTIONS,
        DEFAULT_PROVIDER,
        Decision,
        MESSAGE_TYPES,
        MessageContext,
        NO_EVIDENCE,
    )
    from code.prompts import SYSTEM_PROMPT, build_user_prompt

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE_ROOT = REPO / "code" / "cache" / "routing"

ANTHROPIC_MODEL = "claude-haiku-4-5"


def load_env(path: pathlib.Path = REPO / ".env") -> None:
    """Minimal .env loader. Secrets still come from the environment only —
    this just lets a local .env populate it, mirroring extract_media.py."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()


# ─── stub provider ───────────────────────────────────────────────────────────
#
# A plain keyword/structure heuristic over the context. Deliberately crude —
# this is the M1 skeleton, not the safety gate (M2) or personalization (M3).

SCAM_KEYWORDS = (
    "otp", "one time password", "one-time password", "verify your card",
    "verify card", "verify your account", "verify account", "verify now",
    "verify your identity", "login link", "login code", "reset your password",
    "reset password", "confirm your password", "confirm password",
    "account will be blocked", "account has been blocked", "temporarily blocked",
    "account suspended", "suspend your account", "kyc update", "complete your kyc",
    "wallet verification", "urgent action required", "reactivate your account",
    "click here to verify", "claim your prize", "you have won", "lottery winner",
    "gift card code", "share the otp", "share your otp", "reply with the otp",
    "reply with your otp", "6 digit", "six digit", "security alert",
    "unusual login attempt", "update your payment details", "your payment failed",
    "reattempt fee", "release package",
)

PROMO_KEYWORDS = (
    "% off", "percent off", "sale", "discount", "offer ends", "limited time",
    "buy now", "shop now", "book now", "deal of the day", "promo code",
    "use code", "free shipping", "new arrivals", "unsubscribe", "reply stop",
    "sign up now", "exclusive offer", "save big", "up to 40%", "flat",
)

GREETING_PHRASES = (
    "good morning", "good night", "good evening", "stay positive", "keep smiling",
    "share blessings", "have a blessed day", "have a nice day", "jai shree",
    "happy sunday", "happy monday", "good afternoon",
)

FORWARD_PHRASES = (
    "fwd as received", "forwarding because", "pls forward", "please forward",
    "forwarded as received", "sharing here in case it helps",
)

URGENT_KEYWORDS = (
    "asap", "urgent", "immediately", "right away", "deadline", "eod",
    "need you to", "please join", "reply once", "as soon as possible",
)

PAYMENT_KEYWORDS = (
    "payment", "invoice", "amount due", "bill", "pay now", "emi", "autopay",
    "receipt", "due today", "outstanding balance",
)

EVENT_KEYWORDS = (
    "meeting", "event", "schedule", "reminder", "rsvp", "walkathon", "workshop",
    "agm", "ticket", "showtime",
)


def _any_kw(text_lower: str, keywords: tuple[str, ...]) -> bool:
    """Word-boundary keyword match. Plain substring matching false-positives
    on short keywords (e.g. "emi" inside "reminder", "bill" inside
    "billboard"), so every keyword is matched as a whole word/phrase."""
    pattern = r"\b(?:" + "|".join(re.escape(k.strip()) for k in keywords) + r")\b"
    return re.search(pattern, text_lower) is not None


def _combined_text(ctx: MessageContext) -> str:
    parts = []
    if ctx.message.message_text:
        parts.append(ctx.message.message_text)
    if ctx.media is not None and ctx.media.available and ctx.media.text:
        parts.append(ctx.media.text)
    return "\n".join(parts)


def _mentions_user(text_lower: str, user_id: str) -> bool:
    return bool(user_id) and f"@{user_id.lower()}" in text_lower


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _classify_stub(ctx: MessageContext, text: str) -> tuple[str, str, str, float]:
    """Returns (action, message_type, reason, confidence) before any
    group-mute adjustment. Crude, ordered priority checks."""
    lower = text.lower()
    m = ctx.message

    # RISK IS NOT THIS STAGE'S JOB (M2). code/safety.py owns scam/spam/injection
    # and force-mutes before anything reaches here, so every message the router
    # sees has already been cleared. The prompt-injection, domain-mismatch and
    # scam-keyword branches that used to live here have moved to the gate.
    #
    # They were not merely redundant, they were wrong: the naive domain check
    # flagged verified senders using a link shortener (Thrillophilia), and the
    # keyword check flagged FedEx for saying "no payment or OTP is required".
    # Re-deriving risk here would also reintroduce exactly the failure the blind
    # gate exists to prevent, since this stage CAN see engagement history and
    # could talk itself out of a correct flag.

    if ctx.business or m.conversation_type == "business":
        biz_name = ctx.business.get("display_name") or "This business"
        opted_out = (
            ctx.business_history.get("allows_promotions") == "0"
            or bool(ctx.business_history.get("promotions_opted_out_at"))
        )
        is_promo = _any_kw(lower, PROMO_KEYWORDS)
        engaged = (
            ctx.business_history.get("messages_replied_30d") not in (None, "", "0")
            or ctx.business_history.get("messages_opened_30d") not in (None, "", "0")
        )
        if is_promo:
            if opted_out:
                return (
                    "mute", "promotion",
                    f"The user has opted out of or dismissed promotional messages from "
                    f"{biz_name} before.",
                    0.81,
                )
            return (
                "digest", "promotion",
                f"Promotional message from {biz_name}; useful but not time-sensitive.",
                0.80,
            )
        if _any_kw(lower, PAYMENT_KEYWORDS):
            return (
                "notify" if engaged else "digest", "payment",
                f"{biz_name} sent a payment or account-related update.",
                0.80,
            )
        return (
            "digest", "business_update",
            f"Informational update from {biz_name}, not time-critical.",
            0.72,
        )

    if _any_kw(lower, GREETING_PHRASES) or (
        m.forwarded_count >= 3 and _any_kw(lower, FORWARD_PHRASES)
    ):
        return (
            "mute", "greeting",
            "The message is a generic forwarded greeting with no personal content.",
            0.80,
        )

    if m.forwarded_count > 0 or _any_kw(lower, FORWARD_PHRASES):
        if m.forwarded_count >= 3:
            return (
                "mute", "forward",
                "A repeatedly forwarded message with no original content from this sender.",
                0.78,
            )
        return (
            "digest", "forward",
            "A forwarded message that may be relevant but is not urgent.",
            0.70,
        )

    if _mentions_user(lower, m.user_id) or _any_kw(lower, URGENT_KEYWORDS):
        return (
            "notify", "urgent" if _any_kw(lower, URGENT_KEYWORDS) else "personal",
            "The message directly addresses the user with a time-sensitive ask.",
            0.83,
        )

    if _any_kw(lower, PAYMENT_KEYWORDS):
        return (
            "notify", "payment",
            "The message concerns a payment or amount due for this user.",
            0.78,
        )

    if _any_kw(lower, EVENT_KEYWORDS):
        return (
            "digest", "event",
            "An event or schedule update that is useful but can wait.",
            0.75,
        )

    if not lower.strip():
        return (
            "digest", "unknown",
            "No message text or usable media content is available to route confidently.",
            0.50,
        )

    if m.conversation_type == "personal":
        return (
            "notify", "personal",
            "A personal message from a direct contact with no risk signals.",
            0.72,
        )

    return (
        "digest", "unknown",
        "No strong signal for urgency, risk, or repetition was found in this message.",
        0.60,
    )


def _apply_group_mute_downgrade(action: str, ctx: MessageContext, lower: str) -> str:
    if ctx.membership.get("group_muted_by_user") != "1":
        return action
    if _mentions_user(lower, ctx.message.user_id):
        return action  # a direct @mention pierces an otherwise-muted group
    if action == "notify":
        return "digest"
    if action == "digest":
        return "mute"
    return action


def _pick_evidence_stub(ctx: MessageContext, action: str, message_type: str) -> list[str]:
    """Crude relevance + outcome scoring over this user's message_history."""
    scored: list[tuple[int, str]] = []
    m = ctx.message
    for h in ctx.history:
        mid = h.get("message_id")
        if not mid:
            continue
        score = 0
        if m.business_id and h.get("business_id") == m.business_id:
            score += 2
        if m.sender_user_id and h.get("sender_user_id") == m.sender_user_id:
            score += 2
        if m.group_id and h.get("group_id") == m.group_id:
            score += 1
        htext = (h.get("message_text") or "").lower()
        if message_type == "scam" and _any_kw(htext, SCAM_KEYWORDS):
            score += 2
        if message_type == "promotion" and _any_kw(htext, PROMO_KEYWORDS):
            score += 1
        if message_type in ("greeting", "forward"):
            try:
                if int(h.get("forwarded_count") or 0) > 0:
                    score += 1
            except ValueError:
                pass
        if score == 0:
            continue
        ev = ctx.events.get(mid, {})
        if action == "mute":
            if ev.get("message_reported") == "1":
                score += 3
            if ev.get("muted_after_message") == "1":
                score += 2
            if ev.get("notification_dismissed") == "1":
                score += 1
        elif action == "notify":
            if ev.get("message_replied") == "1":
                score += 2
            if ev.get("message_opened") == "1":
                score += 1
        elif action == "digest":
            if ev.get("message_opened") == "1" and ev.get("message_replied") != "1":
                score += 1
        scored.append((score, mid))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [mid for _, mid in scored[:2]]


def route_stub(ctx: MessageContext) -> Decision:
    text = _combined_text(ctx)
    action, message_type, reason, confidence = _classify_stub(ctx, text)
    action = _apply_group_mute_downgrade(action, ctx, text.lower())
    evidence = _pick_evidence_stub(ctx, action, message_type)
    return Decision(
        message_id=ctx.message.message_id,
        action=action,
        message_type=message_type,
        reason=reason,
        confidence=_clamp01(confidence),
        evidence_message_ids=evidence,
    )


# ─── LLM providers ───────────────────────────────────────────────────────────

def _post(url: str, payload: dict, headers: dict, timeout: int | None = None) -> dict:
    # Retries transient 429/5xx and read timeouts — see code/net.py. A single
    # 503 previously aborted a whole run and discarded every uncached call.
    # timeout defaults to net.DEFAULT_TIMEOUT_SECONDS; 120s was too short for
    # the larger NIM models and produced mid-run socket timeouts.
    try:
        from net import DEFAULT_TIMEOUT_SECONDS, post_json          # noqa: PLC0415
    except ImportError:
        from code.net import DEFAULT_TIMEOUT_SECONDS, post_json     # noqa: PLC0415
    return post_json(url, payload, headers,
                     timeout=timeout or DEFAULT_TIMEOUT_SECONDS)


def _call_anthropic(ctx: MessageContext) -> tuple[str, dict]:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "temperature": 0,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_user_prompt(ctx)}],
    }
    data = _post(
        "https://api.anthropic.com/v1/messages",
        payload,
        {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    return text, {"model": data.get("model", ANTHROPIC_MODEL)}


def _call_nvidia(ctx: MessageContext) -> tuple[str, dict]:
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set")
    base_url = os.environ.get("NVIDIA_BASE_URL")
    if not base_url:
        raise RuntimeError("NVIDIA_BASE_URL is not set")
    model = os.environ.get("NVIDIA_MODEL")
    if not model:
        raise RuntimeError("NVIDIA_MODEL is not set")
    payload = {
        "model": model,
        "temperature": 0,
        # Explicit budget: without it the provider default truncated replies
        # mid-JSON (msg_056), and the salvage path had to recover the answer.
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(ctx)},
        ],
    }
    data = _post(
        base_url.rstrip("/") + "/chat/completions",
        payload,
        {"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    choices = data.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
    return text, {"model": model}


#: Field-level salvage for output that is valid enough to read but not to parse.
_SALVAGE = {
    "action": r'"action"\s*:\s*"([a-z_]+)"',
    "message_type": r'"message_type"\s*:\s*"([a-z_]+)"',
    "reason": r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"',
    "confidence": r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)',
}


def _balanced_objects(text: str):
    """Yield every brace-balanced span, string-aware, outermost-first."""
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                yield text[start:i + 1]
                start = -1


def _extract_json(text: str) -> dict:
    """Tolerant JSON extraction from model output. Never raises.

    Two real failures motivated this. The naive first-brace-to-last-brace scan
    returned nothing when the reply was **truncated** mid-string (msg_056 —
    whose salvageable content was `"action":"notify"`, the correct answer), and
    grabbed a malformed outer span when the model **thought aloud before
    committing** (msg_092 — `{"action: Wait, need correct key...` followed by a
    valid object). Both were silently replaced by a constant decision.
    """
    if not text:
        return {}

    # 1. Prefer a genuinely parseable object. Later objects win: a model that
    #    corrects itself puts the real answer last.
    best: dict = {}
    for span in _balanced_objects(text):
        try:
            parsed = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("action"):
            best = parsed
    if best:
        return best

    # 2. Nothing parsed — salvage individual fields from the raw text. A
    #    truncated reply still carries the decision in its first few keys.
    salvaged: dict = {}
    for key, pattern in _SALVAGE.items():
        match = re.search(pattern, text)
        if match:
            salvaged[key] = match.group(1)
    ids = re.search(r'"evidence_message_ids"\s*:\s*\[([^\]]*)\]', text)
    if ids:
        salvaged["evidence_message_ids"] = re.findall(r'"([^"]+)"', ids.group(1))
    return salvaged if salvaged.get("action") else {}


def _coerce_confidence(value) -> Optional[float]:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return None
    if c != c:  # nan
        return None
    return _clamp01(c)


def _validate_decision(raw: dict, message_id: str) -> Decision:
    if not isinstance(raw, dict) or not raw:
        return Decision(
            message_id=message_id, action="digest", message_type="unknown",
            reason="Model output was not valid JSON; used a safe fallback decision.",
            confidence=0.5, evidence_message_ids=[],
        )

    notes = []
    action = raw.get("action")
    if action not in ACTIONS:
        notes.append(f"action {action!r} was invalid")
        action = "digest"

    message_type = raw.get("message_type")
    if message_type not in MESSAGE_TYPES:
        notes.append(f"message_type {message_type!r} was invalid")
        message_type = "unknown"

    confidence = _coerce_confidence(raw.get("confidence"))
    if confidence is None:
        notes.append("confidence was missing or unparseable")
        confidence = 0.5

    reason = str(raw.get("reason") or "").strip().replace("\n", " ").replace("\r", " ")
    if not reason:
        reason = "Model did not provide a reason."

    evidence_raw = raw.get("evidence_message_ids")
    evidence = []
    if isinstance(evidence_raw, list):
        for e in evidence_raw:
            e = str(e).strip()
            if e and e.lower() != NO_EVIDENCE:
                evidence.append(e)
    evidence = evidence[:2]

    if notes:
        reason = (reason + " (validation fallback: " + "; ".join(notes) + ")").strip()

    return Decision(
        message_id=message_id, action=action, message_type=message_type,
        reason=reason[:500], confidence=confidence, evidence_message_ids=evidence,
    )


def _cache_path(provider: str, message_id: str) -> pathlib.Path:
    return CACHE_ROOT / provider / f"{message_id}.json"


def _load_cache(provider: str, message_id: str) -> Optional[dict]:
    p = _cache_path(provider, message_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(provider: str, message_id: str, payload: dict) -> None:
    p = _cache_path(provider, message_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _apply_m4(ctx: MessageContext, decision: Decision) -> Decision:
    """M4: replace the model's evidence with deterministic retrieval, and
    calibrate its confidence.

    Evidence is a retrieval problem, not a reasoning one — it is rankable
    against measurable criteria (topical overlap, same conversation, whether
    the recorded outcome explains this action), so a scored search beats asking
    the model to pick from a truncated history window. Doing it here rather
    than in the prompt also keeps evidence identical on the rules and LLM
    paths, and means changing the ranking never requires re-calling the API.

    Confidence keeps the model's own number as one input rather than
    discarding it, but does not trust it alone: it returned 0.50 for msg_056,
    the spec's own carve-out example.

    Applied AFTER the cache read, so the cache stores raw model output and
    this logic can be revised without invalidating it.
    """
    try:
        from personalize import signals_for      # noqa: PLC0415
        from evidence import select_evidence     # noqa: PLC0415
        from confidence import calibrate         # noqa: PLC0415
    except ImportError:
        from code.personalize import signals_for     # noqa: PLC0415
        from code.evidence import select_evidence    # noqa: PLC0415
        from code.confidence import calibrate        # noqa: PLC0415

    evidence_ids = select_evidence(ctx, decision.action)
    decision.evidence_message_ids = evidence_ids
    decision.confidence = calibrate(
        decision.action, decision.message_type, evidence_ids,
        signals=signals_for(ctx), model_confidence=decision.confidence,
    )
    return decision


def _rules_fallback(ctx: MessageContext) -> Decision:
    """Deterministic rules decision, used when the model output cannot be read."""
    try:
        from personalize import personalize      # noqa: PLC0415
    except ImportError:
        from code.personalize import personalize  # noqa: PLC0415
    decision = personalize(ctx)
    decision.reason = f"[rules fallback: model output unreadable] {decision.reason}"
    return decision


def _route_llm(ctx: MessageContext, provider: str, call_fn) -> Decision:
    message_id = ctx.message.message_id
    cached = _load_cache(provider, message_id)
    if cached is not None:
        return _apply_m4(ctx, _validate_decision(cached, message_id))

    text, meta = call_fn(ctx)  # RuntimeError on missing key propagates, by design
    raw = _extract_json(text)
    if not raw:
        # Unparseable and unsalvageable. Falling back to a constant
        # digest/unknown silently answered the spec's own carve-out example
        # with an error handler. We have a working rules engine — use it, and
        # say so in the reason so the degradation is visible in the output.
        decision = _rules_fallback(ctx)
    else:
        decision = _validate_decision(raw, message_id)
    _save_cache(provider, message_id, {
        "action": decision.action,
        "message_type": decision.message_type,
        "reason": decision.reason,
        "confidence": decision.confidence,
        "evidence_message_ids": decision.evidence_message_ids,
        "provider": provider,
        "model": meta.get("model"),
        "raw_text": text[:4000],
    })
    return _apply_m4(ctx, decision)


def route_anthropic(ctx: MessageContext) -> Decision:
    return _route_llm(ctx, "anthropic", _call_anthropic)


def route_nvidia(ctx: MessageContext) -> Decision:
    return _route_llm(ctx, "nvidia", _call_nvidia)


# ─── dispatch ─────────────────────────────────────────────────────────────────

_PROVIDERS = {
    "stub": route_stub,
    "anthropic": route_anthropic,
    "nvidia": route_nvidia,
}


def route(ctx: MessageContext, provider: str = DEFAULT_PROVIDER) -> Decision:
    try:
        fn = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(f"unknown provider: {provider!r}, expected one of {sorted(_PROVIDERS)}")
    return fn(ctx)


def route_all(contexts: list[MessageContext], provider: str = DEFAULT_PROVIDER) -> list[Decision]:
    return [route(ctx, provider) for ctx in contexts]


# ─── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        from contracts import MediaExtract, Message
    except ImportError:
        from code.contracts import MediaExtract, Message

    def _msg(**kw) -> Message:
        base = dict(
            message_id="test", user_id="u_001", conversation_type="group",
            group_id="", business_id="", sender_user_id="", created_at="2026-07-31 10:00",
            message_text="", media_type="", media_id="", forwarded_count=0,
        )
        base.update(kw)
        return Message(**base)

    fixtures = [
        (
            "normal group message",
            MessageContext(
                message=_msg(
                    message_id="fx_group_normal", group_id="group_001",
                    sender_user_id="u_002",
                    message_text="Reminder: society AGM tomorrow at 6pm in the clubhouse, "
                                  "please attend if you can.",
                ),
                group={"group_name": "Test Society", "group_type": "society", "member_count": "40"},
                membership={"role": "member", "group_muted_by_user": "0"},
                history=[],
                events={},
            ),
        ),
        (
            "business promo (opted in)",
            MessageContext(
                message=_msg(
                    message_id="fx_business_promo", conversation_type="business",
                    business_id="biz_test",
                    message_text="Flat 40% off on all items this weekend only! Shop now "
                                  "and use code SAVE40. Reply STOP to unsubscribe.",
                ),
                business={
                    "display_name": "Test Store", "official_domain": "teststore.com",
                    "domain_used_by_sender": "teststore.com",
                },
                business_history={"allows_promotions": "1", "messages_opened_30d": "3"},
                history=[],
                events={},
            ),
        ),
        (
            "obvious OTP phishing",
            MessageContext(
                message=_msg(
                    message_id="fx_otp_phish", conversation_type="personal",
                    sender_user_id="u_099",
                    message_text="Your workspace access will expire today. Reply with the "
                                  "6 digit login code you just received so we can keep your "
                                  "account active.",
                ),
                history=[], events={},
            ),
        ),
        (
            "empty message_text, voice media with transcript",
            MessageContext(
                message=_msg(
                    message_id="fx_empty_text_voice", conversation_type="business",
                    business_id="biz_test2", media_type="voice", media_id="vn_test",
                    message_text="",
                ),
                media=MediaExtract(
                    media_id="vn_test",
                    text="Hi, this is a reminder about tomorrow's meeting at 10am.",
                    available=True,
                ),
                business={
                    "display_name": "Test Clinic", "official_domain": "testclinic.com",
                    "domain_used_by_sender": "testclinic.com",
                },
                business_history={},
                history=[], events={},
            ),
        ),
        (
            "media unavailable (extraction missing)",
            MessageContext(
                message=_msg(
                    message_id="fx_media_unavailable", conversation_type="group",
                    group_id="group_002", sender_user_id="u_003",
                    media_type="image", media_id="img_missing", message_text="",
                ),
                media=MediaExtract(media_id="img_missing", text="", available=False,
                                    error="extraction not yet run"),
                group={"group_name": "Test Group"},
                membership={"group_muted_by_user": "0"},
                history=[], events={},
            ),
        ),
    ]

    for label, ctx in fixtures:
        d = route_stub(ctx)
        assert d.action in ACTIONS, d
        assert d.message_type in MESSAGE_TYPES, d
        assert 0.0 <= d.confidence <= 1.0, d
        print(f"[{label}]")
        print(f"  action={d.action} message_type={d.message_type} confidence={d.confidence}")
        print(f"  reason={d.reason}")
        print(f"  evidence={d.evidence_message_ids or 'none'}")
        print()

    print(f"All {len(fixtures)} fixtures produced a valid Decision via the stub provider.")
