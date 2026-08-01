#!/usr/bin/env python3
"""One-time media extraction for the Message Notification Router.

Reads image media referenced by dataset/messages.csv and dataset/message_history.csv,
runs OCR + description through a pluggable vision provider, and writes the result to
a JSON cache keyed by media_id. The router reads only the cache, never a provider —
that is what makes reruns reproducible (see DECISIONS.md).

Providers are selected with VISION_PROVIDER (env) or --provider:
    gemini  -> Google AI Studio (default; see GEMINI_VISION_MODEL)
    nim     -> NVIDIA NIM, OpenAI-compatible chat/completions (NIM_VISION_MODEL)
    both    -> run each in turn and write a side-by-side comparison

Stdlib only, so the submission runs without a package install step.

    python3 code/extract_media.py --provider both --only img_002,img_011
"""

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE_DIR = REPO / "code" / "cache"

# gemini-2.5-flash still appears in the models list but 404s for keys created
# after its retirement ("no longer available to new users"). Pinned to an
# explicit current model rather than the -latest alias, which can drift.
GEMINI_MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-3.6-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_NIM_VISION_MODEL = "nvidia/nemotron-nano-12b-v2-vl"

# Identical prompt for every provider — otherwise the comparison measures the
# prompt, not the model. Fields are chosen for what the router actually needs:
# verbatim text drives the safety gate, key details drive urgency/payment
# detection, and the type/description drive message_type.
OCR_PROMPT = """This image was attached to a WhatsApp message. Extract it for an automated message router.

Reply in exactly this format, with these four labels and nothing else:

VERBATIM_TEXT:
<every piece of text visible in the image, in reading order, preserving line breaks. Include small print, footers, watermarks, and disclaimers. Write NONE if the image contains no text.>

DOCUMENT_TYPE:
<one of: poster, screenshot, scanned_document, photo, receipt, chart, other>

DESCRIPTION:
<one or two sentences describing what this image is and what it is for.>

KEY_DETAILS:
<one per line: amounts, dates, deadlines, phone numbers, URLs, payment or account details, and brand or organisation names. Write NONE if there are none.>"""


def load_env(path=REPO / ".env"):
    """Minimal .env loader. Secrets come from the environment only."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _post(url, payload, headers, timeout=180):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _rel(path):
    """Repo-relative path when possible; absolute otherwise (--out-dir may be outside)."""
    try:
        return pathlib.Path(path).resolve().relative_to(REPO)
    except ValueError:
        return path


def _b64(path):
    return base64.b64encode(pathlib.Path(path).read_bytes()).decode()


def ocr_gemini(image_path):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    payload = {
        "contents": [{"parts": [
            {"text": OCR_PROMPT},
            {"inline_data": {"mime_type": "image/jpeg", "data": _b64(image_path)}},
        ]}],
        # temperature 0 is the best determinism we can ask of a hosted endpoint;
        # the committed cache is what actually guarantees reproducible reruns.
        #
        # thinkingBudget 0 matters: on Gemini 3.x the token cap covers thinking
        # *plus* output, so with thinking on, a dense image (bank statement,
        # prescription) burns the whole budget reasoning and returns a truncated
        # fragment of raw reasoning instead of the four labels. OCR is
        # transcription, not reasoning — it does not need a thinking budget.
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    data = _post(
        GEMINI_URL.format(model=GEMINI_MODEL) + "?key=" + key,
        payload,
        {"Content-Type": "application/json"},
    )
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"no candidates: {json.dumps(data)[:300]}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    usage = data.get("usageMetadata", {})
    return text, {
        "model": GEMINI_MODEL,
        "input_tokens": usage.get("promptTokenCount"),
        "output_tokens": usage.get("candidatesTokenCount"),
        "total_tokens": usage.get("totalTokenCount"),
        "finish_reason": candidates[0].get("finishReason"),
    }


def ocr_nim(image_path, model=None):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set")
    model = model or os.environ.get("NIM_VISION_MODEL", DEFAULT_NIM_VISION_MODEL)
    base = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": OCR_PROMPT},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64," + _b64(image_path)}},
        ]}],
        "temperature": 0,
        "max_tokens": 2048,
    }
    data = _post(
        base + "/chat/completions",
        payload,
        {"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"no choices: {json.dumps(data)[:300]}")
    text = (choices[0].get("message", {}).get("content") or "").strip()
    usage = data.get("usage", {})
    return text, {
        "model": model,
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "finish_reason": choices[0].get("finish_reason"),
    }


PROVIDERS = {"gemini": ocr_gemini, "nim": ocr_nim}

REQUIRED_LABELS = ("VERBATIM_TEXT", "DOCUMENT_TYPE", "DESCRIPTION", "KEY_DETAILS")


def validate(text, meta):
    """Return a list of structural problems with an extraction.

    A vision model can fail without erroring: nemotron-nano-12b-v2-vl degenerates
    into a repeated-character loop on scanned forms, burns the whole token budget,
    and returns a 200 with unusable text. Caching that as a success would poison
    every downstream routing decision for that image, so treat a malformed
    extraction as a failure rather than trusting the HTTP status.
    """
    problems = []
    missing = [l for l in REQUIRED_LABELS if l not in text]
    if missing:
        problems.append("missing labels: " + ",".join(missing))
    if meta.get("finish_reason") in ("length", "MAX_TOKENS"):
        problems.append("truncated (hit max_tokens)")
    # A degenerate loop shows up as one character class dominating the output.
    if text:
        filler = sum(text.count(c) for c in "_-. ")
        if len(text) > 800 and filler / len(text) > 0.5:
            problems.append(f"degenerate output ({filler / len(text):.0%} filler chars)")
    return problems


def load_images():
    """media_id -> absolute path, from dataset/images.csv."""
    import csv
    # file_path in images.csv is relative to dataset/, not the repo root.
    rows = list(csv.DictReader(open(REPO / "dataset" / "images.csv")))
    return {r["image_id"]: REPO / "dataset" / r["file_path"] for r in rows}


def run_provider(provider, image_ids, images, sleep=0.0):
    fn = PROVIDERS[provider]
    out = {}
    for i, mid in enumerate(image_ids, 1):
        path = images[mid]
        started = time.monotonic()
        try:
            text, meta = fn(path)
            err = None
        except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, OSError) as exc:
            detail = ""
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    detail = " :: " + exc.read().decode()[:400]
                except Exception:
                    pass
            text, meta, err = "", {}, f"{type(exc).__name__}: {exc}{detail}"
        elapsed = round(time.monotonic() - started, 2)
        problems = [] if err else validate(text, meta)
        out[mid] = {"text": text, "error": err, "problems": problems,
                    "seconds": elapsed, **meta}
        status = "ERR" if err else ("BAD" if problems else "ok ")
        chars = len(text)
        print(f"  [{provider}] {i}/{len(image_ids)} {mid} {status} {elapsed:>6.2f}s "
              f"{chars:>5d} chars", file=sys.stderr)
        if err:
            print(f"      {err[:220]}", file=sys.stderr)
        elif problems:
            print(f"      {'; '.join(problems)}", file=sys.stderr)
        if sleep and i < len(image_ids):
            time.sleep(sleep)
    return out


def write_comparison(results, image_ids, path):
    """Side-by-side markdown so the two providers can be judged by eye."""
    lines = ["# Vision provider comparison", ""]
    for provider, res in results.items():
        ok = [v for v in res.values() if not v["error"] and not v.get("problems")]
        total_secs = sum(v["seconds"] for v in res.values())
        in_tok = sum(v.get("input_tokens") or 0 for v in res.values())
        out_tok = sum(v.get("output_tokens") or 0 for v in res.values())
        model = next((v.get("model") for v in res.values() if v.get("model")), "?")
        lines += [
            f"- **{provider}** (`{model}`): {len(ok)}/{len(res)} clean, "
            f"{total_secs:.1f}s total, {in_tok} input tokens, {out_tok} output tokens",
        ]
    lines.append("")
    for mid in image_ids:
        lines += [f"## {mid}", ""]
        for provider, res in results.items():
            entry = res.get(mid, {})
            lines += [f"### {provider}", ""]
            if entry.get("error"):
                lines += ["```", f"ERROR: {entry['error'][:800]}", "```", ""]
            elif entry.get("problems"):
                lines += [f"**REJECTED: {'; '.join(entry['problems'])}**", "",
                          "```", entry.get("text", "")[:1500], "```", ""]
            else:
                lines += [
                    f"_{entry.get('seconds')}s · {entry.get('output_tokens')} output tokens_",
                    "", "```", entry.get("text", "")[:4000], "```", "",
                ]
    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=os.environ.get("VISION_PROVIDER", "gemini"),
                    choices=["gemini", "nim", "both"])
    ap.add_argument("--only", help="comma-separated media ids, e.g. img_002,img_011")
    ap.add_argument("--limit", type=int, help="cap number of images")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="seconds between calls (Gemini free tier is RPM-limited)")
    ap.add_argument("--out-dir", default=str(CACHE_DIR))
    args = ap.parse_args()

    load_env()
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    images = load_images()
    if args.only:
        image_ids = [m.strip() for m in args.only.split(",") if m.strip()]
        missing = [m for m in image_ids if m not in images]
        if missing:
            sys.exit(f"unknown media ids: {missing}")
    else:
        image_ids = sorted(images)
    if args.limit:
        image_ids = image_ids[: args.limit]

    providers = list(PROVIDERS) if args.provider == "both" else [args.provider]
    print(f"{len(image_ids)} image(s) x {len(providers)} provider(s)", file=sys.stderr)

    results = {}
    for provider in providers:
        results[provider] = run_provider(provider, image_ids, images, sleep=args.sleep)
        dest = out_dir / f"media_{provider}.json"
        dest.write_text(json.dumps(results[provider], indent=2, sort_keys=True))
        print(f"  -> {_rel(dest)}", file=sys.stderr)

    if len(providers) > 1:
        cmp_path = out_dir / "vision_comparison.md"
        write_comparison(results, image_ids, cmp_path)
        print(f"  -> {_rel(cmp_path)}", file=sys.stderr)


if __name__ == "__main__":
    main()
