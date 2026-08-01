# Message Notification Router — solution

For every message in `dataset/messages.csv`, decide whether to `notify`,
`digest`, or `mute`, and write `output.csv`.

This is the solution README. The repository-root `README.md` is the
organizer's challenge description.

---

## Requirements

**Python 3.9+. No third-party packages, no install step.** The pipeline is
standard library only (`urllib`, not `requests`). Verified on 3.9.6.

There is deliberately no `requirements.txt` — there is nothing to require.

---

## Quick start — no API keys needed

```bash
python code/main.py --provider stub
```

Writes `output.csv` at the repo root and validates it. This path is fully
offline and deterministic: it makes no network calls and needs no credentials.
It is the guaranteed floor — it always produces a submittable `output.csv`.

## Full-quality run

```bash
cp .env.example .env      # then fill in NVIDIA_API_KEY
python code/main.py --provider nvidia
```

Accuracy against the 30 labelled sample rows:

| path | action | message_type |
|---|---|---|
| `--provider stub` (offline) | 70% | 47% |
| `--provider nvidia` (default) | **93%** | **83%** |

Both paths use the same deterministic safety gate; only the personalization
stage differs. See "Why the safety gate ignores `--provider`" below.

---

## Architecture

Three stages. Each message passes through them in order.

```
messages.csv ─┐
              ├─> [context assembly] ─> [1 SAFETY GATE] ─> [2 PERSONALIZATION] ─> [3 WRITER] ─> output.csv
media.json  ──┘                          (blind, rules)      (full context)         + validator
```

**1. Safety gate** (`safety.py`) — decides risk, and can force `mute` with
`scam`/`spam` on its own. It is *blind*: it sees message content plus
structural sender facts (verification status, official vs. used domain,
account age, report counts) but is structurally prevented from seeing the
user's engagement history. `SafetyContext` whitelists every permitted field
and `assert_blind()` fails loudly if an engagement field ever reaches a prompt.

The reason is that the spec requires risk to be muted *"regardless of the
user's usual engagement"*. Ordering alone does not achieve that — a stage that
can see "this user replies to this sender constantly" can rationalise its way
out of a correct flag. Withholding the context is what enforces the rule.

**2. Personalization** (`personalize.py`) — for messages that clear the gate,
chooses notify/digest/mute-for-low-value using group mute state and dismissal
rates, promotion consent (`allows_promotions`, `promotions_opted_out_at`),
relationship staleness, quiet hours, and notification load measured against
each user's own baseline. Signals are always computed and are rendered into
the LLM prompt, so choosing a provider cannot bypass this stage.

**3. Writer + validator** (`writer.py`, `validate.py`) — emits the exact
required columns and re-checks the file from disk the way a grader would.

### Media

Image OCR and voice transcription run **once**, offline, into
`code/cache/media.json`, which is committed. The router never calls a media
provider, so runs are reproducible and the submission needs no Gemini or Groq
key. Regenerate with `code/extract_media.py` / `code/extract_audio.py`.

### Determinism

Every model response is cached under `code/cache/` keyed by `message_id`.
A rerun replays the cache and produces a byte-identical `output.csv`.
Temperature is 0 everywhere, but that alone is not sufficient across
providers — the cache is what makes the guarantee real.

---

## Verify

```bash
python code/validate.py output.csv        # grader-style contract check
python code/gate_m2.py                    # safety-gate assertions
python code/score_samples.py              # accuracy vs labelled samples (rules)
python code/score_samples.py --provider nvidia   # ...and the shipping path
```

`validate.py` checks column order, one row per `message_id`, enum membership,
confidence range, single-line reasons, and that every `evidence_message_ids`
value resolves in `message_history.csv`.

`gate_m2.py` asserts the safety gate mutes the OTP-phishing message and every
impersonation-domain sender, never mutes a trusted sender, and is provably
blind across all 110 prompts.

---

## Configuration

All secrets come from the environment or a local `.env` (gitignored). See
`.env.example` for the full list and per-key free-tier notes.

| Variable | Used by |
|---|---|
| `ROUTER_PROVIDER` | `stub` \| `anthropic` \| `nvidia` — personalization engine |
| `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_MODEL` | NIM personalization |
| `ANTHROPIC_API_KEY` | alternative personalization provider |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | media extraction only; not needed to run the router |
| `SAFETY_PROVIDER` | defaults to `stub`; see below |

### Why the safety gate ignores `--provider`

The gate always runs the deterministic rules even when personalization uses an
LLM. Measured over all 110 rows, the LLM safety classifier force-muted 44
messages against 22 for the rules, and produced 6 false positives on verified,
clean-domain senders — muting HDFC Bank for "vague urgency framing" and a
pharmacy for being an "unverified sender". The gate's contract is that a
trusted sender is never falsely muted, so it stays deterministic.
`--safety-provider` exists only to re-measure that.

---

## Files

| Path | Role |
|---|---|
| `main.py` | entry point; wires the three stages |
| `contracts.py` | shared types, allowed enums, output column order |
| `data.py`, `media_cache.py` | load the 12 CSVs and the media cache |
| `safety.py` | stage 1 — blind safety gate |
| `personalize.py` | stage 2 — personalization signals and rules |
| `router.py`, `prompts.py` | LLM personalization + prompt construction |
| `writer.py`, `validate.py` | stage 3 — output and contract validation |
| `net.py` | HTTP with retry/backoff for transient provider failures |
| `gate_m2.py`, `score_samples.py` | verification and self-evaluation |
| `extract_media.py`, `extract_audio.py` | one-off media extraction (M0) |
| `cache/` | committed media extraction + model response cache |

`../DECISIONS.md` records every non-obvious design call with its alternatives
and trade-offs. `../CHECKLIST.md` records verified status and known gaps.

---

## Known limitations

Recorded honestly rather than omitted; the full list is in `../CHECKLIST.md`.

- **`spam` is never emitted.** Everything the gate catches is deception
  (`scam`); unwanted promotions come out as `mute`/`promotion`. At least one
  labelled sample uses `spam` for a promotional blast, so this costs us rows.
- **Confidence is not calibrated.** The LLM path returns two rows at 0.50
  against a 0.78 floor observed in the samples.
- **Evidence selection is basic** — same-conversation history filtered by
  whether the recorded outcome matches the chosen action, not similarity-ranked.
- Three voice transcripts begin mid-sentence; those rows route on partial text.
