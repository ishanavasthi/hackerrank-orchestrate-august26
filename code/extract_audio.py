#!/usr/bin/env python3
"""M0 voice-note transcription for the Message Notification Router.

Mirrors code/extract_media.py: a pluggable provider switch, an identical task
for every provider so the comparison measures the model rather than the prompt,
and structural validation of every result before anything is cached. The router
reads only the committed cache, never a provider — that is what makes reruns
reproducible (see DECISIONS.md).

Providers are selected with ASR_PROVIDER (env) or --provider:
    groq -> Groq whisper-large-v3-turbo (purpose-built ASR)
    nim  -> NVIDIA NIM nemotron-3-nano-omni (omni-modal, audio in chat)
    both -> run each in turn and write a side-by-side comparison

Stdlib only, so the submission runs without a package install step.

    python3 code/extract_audio.py --provider both --only vn_001,vn_002
"""

import argparse
import base64
import csv
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from extract_media import REPO, _post, _rel, load_env  # noqa: E402

CACHE_DIR = REPO / "code" / "cache"

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = os.environ.get("GROQ_ASR_MODEL", "whisper-large-v3-turbo")
DEFAULT_NIM_AUDIO_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

# Used only by the omni model, which needs to be told to behave like an ASR
# system. Groq's Whisper endpoint transcribes by definition and takes no
# instruction — deliberately left without a `prompt`, because a Whisper prompt
# biases decoding and is a known trigger for repetition loops.
NIM_ASR_INSTRUCTION = (
    "Transcribe this voice note verbatim. Output only the transcript text, with no "
    "commentary, no speaker labels, and no translation. Preserve the original "
    "language, including code-mixed Hindi and English exactly as spoken."
)


def _post_multipart(url, fields, file_field, filename, file_bytes, headers, timeout=180):
    """Minimal multipart/form-data encoder (Groq's audio endpoint needs one)."""
    boundary = "----claude" + uuid.uuid4().hex
    body = bytearray()
    for key, value in fields.items():
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
        ).encode()
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode()
    body += file_bytes
    body += f"\r\n--{boundary}--\r\n".encode()

    headers = dict(headers)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    # Groq sits behind Cloudflare, which rejects urllib's default
    # "Python-urllib/3.x" agent with a 403 (error code 1010).
    headers.setdefault("User-Agent", "hackerrank-orchestrate-router/1.0")
    req = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def asr_groq(audio_path):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    path = pathlib.Path(audio_path)
    data = _post_multipart(
        GROQ_URL,
        # verbose_json is what makes validation possible: it returns duration,
        # detected language, and per-segment no_speech_prob / avg_logprob.
        {"model": GROQ_MODEL, "response_format": "verbose_json", "temperature": "0"},
        "file", path.name, path.read_bytes(),
        {"Authorization": "Bearer " + key},
    )
    segments = data.get("segments") or []
    no_speech = [s.get("no_speech_prob") for s in segments if s.get("no_speech_prob") is not None]
    logprobs = [s.get("avg_logprob") for s in segments if s.get("avg_logprob") is not None]
    return (data.get("text") or "").strip(), {
        "provider": "groq",
        "model": GROQ_MODEL,
        "language": data.get("language"),
        "duration_seconds": data.get("duration"),
        "segments": len(segments),
        "max_no_speech_prob": round(max(no_speech), 3) if no_speech else None,
        "min_avg_logprob": round(min(logprobs), 3) if logprobs else None,
    }


def asr_nim(audio_path, model=None):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set")
    model = model or os.environ.get("NIM_AUDIO_MODEL", DEFAULT_NIM_AUDIO_MODEL)
    base = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    audio_b64 = base64.b64encode(pathlib.Path(audio_path).read_bytes()).decode()
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": NIM_ASR_INSTRUCTION},
            # NOTE: the OpenAI-style "input_audio" block is accepted by this
            # endpoint but the audio is silently dropped — the model replies
            # "I'm unable to transcribe ... without the audio file". NIM wants
            # an "audio_url" data URI. The failure mode is a polite refusal at
            # HTTP 200, which is why validate() screens for refusal language.
            {"type": "audio_url",
             "audio_url": {"url": "data:audio/mpeg;base64," + audio_b64}},
        ]}],
        "temperature": 0,
        "max_tokens": 2048,
    }
    data = _post(
        base + "/chat/completions", payload,
        {"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"no choices: {json.dumps(data)[:300]}")
    usage = data.get("usage", {})
    return (choices[0].get("message", {}).get("content") or "").strip(), {
        "provider": "nim",
        "model": model,
        "output_tokens": usage.get("completion_tokens"),
        "finish_reason": choices[0].get("finish_reason"),
    }


PROVIDERS = {"groq": asr_groq, "nim": asr_nim}


def validate(text, meta):
    """Structural problems with a transcript.

    ASR fails differently from OCR. Whisper's signature failure is a
    hallucination loop on silence or noise — it emits a plausible sentence and
    then repeats it, or invents subtitle credits — and it returns HTTP 200 while
    doing so. An omni model can instead ignore the audio and answer the prompt
    conversationally. Both would be cached as good transcripts and would poison
    every routing decision for that voice note, so check the shape rather than
    trusting the status code.
    """
    problems = []
    if not text:
        problems.append("empty transcript")
        return problems

    if meta.get("finish_reason") in ("length", "MAX_TOKENS"):
        problems.append("truncated (hit max_tokens)")

    # Repetition loop: few unique sentences relative to total sentence count.
    sentences = [s.strip().lower() for s in text.replace("\n", " ").split(".") if s.strip()]
    if len(sentences) >= 6 and len(set(sentences)) <= len(sentences) / 3:
        problems.append(
            f"repetition loop ({len(set(sentences))} unique of {len(sentences)} sentences)")

    # A model that answered the instruction instead of transcribing. Normalise
    # smart punctuation first: nemotron-omni refuses with a curly apostrophe
    # ("I\u2019m unable to transcribe..."), which slipped straight past an
    # earlier version of this check that matched a straight apostrophe at
    # string start only. Scan the opening of the text, not just index 0.
    lowered = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    for tell in ("i cannot", "i can't", "i'm unable", "i am unable", "unable to transcribe",
                 "as an ai", "here is the transcript", "sure, here", "please provide",
                 "could you please provide", "no audio", "without the audio"):
        if tell in lowered[:200]:
            problems.append(f"model addressed the prompt instead of transcribing ({tell!r})")
            break

    # Whisper marks non-speech confidently; a high value with real text returned
    # usually means it hallucinated over silence.
    if (meta.get("max_no_speech_prob") or 0) > 0.6:
        problems.append(f"likely non-speech (max_no_speech_prob={meta['max_no_speech_prob']})")

    # Sanity-check transcript length against audio duration. Natural speech runs
    # well above 3 characters/second; far below that means most of the audio
    # produced nothing.
    duration = meta.get("duration_seconds")
    if duration and duration > 5 and len(text) / duration < 3:
        problems.append(
            f"suspiciously short for {duration:.0f}s of audio ({len(text)} chars)")
    return problems


def load_voice_notes():
    """voice_note_id -> absolute path (file_path is relative to dataset/)."""
    rows = list(csv.DictReader(open(REPO / "dataset" / "voice_notes.csv")))
    return {r["voice_note_id"]: REPO / "dataset" / r["file_path"] for r in rows}


def run_provider(provider, ids, files, sleep=0.0):
    fn = PROVIDERS[provider]
    out = {}
    for i, vid in enumerate(ids, 1):
        started = time.monotonic()
        try:
            text, meta = fn(files[vid])
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
        out[vid] = {"kind": "audio", "transcript": text, "error": err,
                    "problems": problems, "seconds": elapsed, **meta}
        status = "ERR" if err else ("BAD" if problems else "ok ")
        dur = meta.get("duration_seconds")
        dur_s = f"{dur:>5.1f}s audio" if dur else " " * 11
        print(f"  [{provider}] {i}/{len(ids)} {vid} {status} {elapsed:>6.2f}s "
              f"{dur_s} {len(text):>5d} chars", file=sys.stderr)
        if err:
            print(f"      {err[:220]}", file=sys.stderr)
        elif problems:
            print(f"      {'; '.join(problems)}", file=sys.stderr)
        if sleep and i < len(ids):
            time.sleep(sleep)
    return out


def write_comparison(results, ids, path):
    lines = ["# ASR provider comparison", ""]
    for provider, res in results.items():
        clean = [v for v in res.values() if not v["error"] and not v["problems"]]
        secs = sum(v["seconds"] for v in res.values())
        model = next((v.get("model") for v in res.values() if v.get("model")), "?")
        lines.append(f"- **{provider}** (`{model}`): {len(clean)}/{len(res)} clean, "
                     f"{secs:.1f}s wall clock")
    lines.append("")
    for vid in ids:
        lines += [f"## {vid}", ""]
        for provider, res in results.items():
            e = res.get(vid, {})
            lines += [f"### {provider}", ""]
            if e.get("error"):
                lines += ["```", f"ERROR: {e['error'][:600]}", "```", ""]
            else:
                if e.get("problems"):
                    lines += [f"**REJECTED: {'; '.join(e['problems'])}**", ""]
                meta = f"_{e.get('seconds')}s"
                if e.get("language"):
                    meta += f" · language={e['language']}"
                if e.get("duration_seconds"):
                    meta += f" · {e['duration_seconds']:.1f}s audio"
                lines += [meta + "_", "", "```", e.get("transcript", "")[:3000], "```", ""]
    path.write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=os.environ.get("ASR_PROVIDER", "groq"),
                    choices=["groq", "nim", "both"])
    ap.add_argument("--only", help="comma-separated voice note ids, e.g. vn_001,vn_002")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--out-dir", default=str(CACHE_DIR))
    args = ap.parse_args()

    load_env()
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = load_voice_notes()
    if args.only:
        ids = [v.strip() for v in args.only.split(",") if v.strip()]
        missing = [v for v in ids if v not in files]
        if missing:
            sys.exit(f"unknown voice note ids: {missing}")
    else:
        ids = sorted(files)
    if args.limit:
        ids = ids[: args.limit]

    providers = list(PROVIDERS) if args.provider == "both" else [args.provider]
    print(f"{len(ids)} voice note(s) x {len(providers)} provider(s)", file=sys.stderr)

    results = {}
    for provider in providers:
        results[provider] = run_provider(provider, ids, files, sleep=args.sleep)
        dest = out_dir / f"audio_{provider}.json"
        dest.write_text(json.dumps(results[provider], indent=2, sort_keys=True))
        print(f"  -> {_rel(dest)}", file=sys.stderr)

    if len(providers) > 1:
        cmp_path = out_dir / "asr_comparison.md"
        write_comparison(results, ids, cmp_path)
        print(f"  -> {_rel(cmp_path)}", file=sys.stderr)


if __name__ == "__main__":
    main()
