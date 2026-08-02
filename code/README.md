# Message Notification Router — solution

For every message in `dataset/messages.csv`, decide whether to **`notify`**
(interrupt now), **`digest`** (wait for later), or **`mute`** (suppress), and
write `output.csv` with the required columns:

```text
message_id,action,message_type,reason,confidence,evidence_message_ids
```

This package is the solution itself. It is self-contained: everything needed to
read, review and run the router is in this directory.

---

## At a glance

| | |
|---|---|
| **Run it** | `python code/main.py --provider nvidia` (from the parent of `code/`) |
| **API key needed?** | **No.** All 114 model responses are committed; the run replays from disk. |
| **Install step?** | **None.** Python 3.9+, standard library only. |
| **Verify it** | `python code/eval_harness.py --provider nvidia` → `ALL CHECKS PASS` |
| **Accuracy** (30 labelled samples) | **action 93%** (28/30), **message_type 83%** (25/30) |
| **Reproducibility** | Byte-identical `output.csv`, md5 `b0dabbce3443bb13f50c4a6afc77cb03` |
| **Rows produced** | 110, exactly one per `message_id` |

---

## Contents

- [1. Setup](#1-setup) — requirements, layout, run, verify
- [2. Approach](#2-approach) — the three stages, evidence, confidence, determinism
- [3. Why the cache is the artifact](#3-why-the-cache-is-the-artifact)
- [4. Known limitations](#4-known-limitations)
- [5. File map](#5-file-map)

---

# 1. Setup

## 1.1 Requirements

**Python 3.9+. No third-party packages, no install step.** The pipeline is
standard library only (`urllib`, not `requests`). Verified on 3.9.6.

There is deliberately no `requirements.txt` — there is nothing to require.

## 1.2 Expected layout

This zip contains the `code/` directory only. Two things the router reads at
runtime are deliberately **not** included:

| Needed | Where it must be | Why it is not here |
|---|---|---|
| `dataset/` | sibling of `code/` | organiser-provided; excluded from the submission |
| `.env` | sibling of `code/` | secrets are never committed, and none is required — see below |

Arrange them like this:

```text
<work dir>/
├── code/            <- this package
│   ├── main.py
│   └── cache/       <- committed OCR/ASR + model-response cache
├── dataset/         <- organiser-provided
│   ├── messages.csv
│   └── media/
├── .env             <- optional; only for messages not already cached
└── output.csv       <- written here
```

**Every command below is run from `<work dir>`, the parent of `code/`.** Paths
are resolved relative to `code/`'s parent, so the layout above is the only
requirement — the working directory itself does not matter.

## 1.3 Run it

**No API key is needed to reproduce the shipped results.** All 114 model
responses are committed under `code/cache/routing/nvidia/`, so a run replays
from disk. A key is only required to route a message that is *not* in the
cache — a new dataset, or a changed prompt.

```bash
python code/main.py --provider nvidia
```

This is the full-quality shipping path and it runs **offline with no
credentials**. Expected output:

```text
  110 messages
  media extracted for 23/23 media-bearing messages
  force-muted 22/110 on risk grounds
PASS 110 rows
```

The run is **byte-identical to the predictions CSV submitted alongside this
package**:

```text
md5  b0dabbce3443bb13f50c4a6afc77cb03
```

That is the point: the 93%/83% figure does not have to be taken on trust. With
no `.env` present and every provider key unset, the run still reproduces that
exact file:

```bash
env -u NVIDIA_API_KEY -u ANTHROPIC_API_KEY \
    -u GEMINI_API_KEY -u GROQ_API_KEY \
    python code/main.py --provider nvidia
md5 -q output.csv          # -> b0dabbce3443bb13f50c4a6afc77cb03
```

Verified by exporting the committed tree into an empty directory with no `.env`
and those four variables unset: same row counts, same 22 force-mutes, identical
MD5 before and after.

### Useful flags

| Flag | Default | Purpose |
|---|---|---|
| `--provider` | `nvidia` | `stub` \| `anthropic` \| `nvidia` — personalization engine |
| `--safety-provider` | `stub` | Reasoning engine for the safety gate; see [§2.2](#22-stage-1--safety-gate-blind) |
| `--dataset` | `../dataset` | Dataset directory |
| `--out` | `../output.csv` | Where to write predictions |
| `--limit N` | all | Route only the first N messages (smoke tests) |
| `--validate-only` | off | Skip routing; just validate the existing `--out` file |

## 1.4 Verify

```bash
python code/eval_harness.py --provider nvidia    # runs all four checks below
```

Prints `ALL CHECKS PASS` and `action 28/30 = 93%, message_type 25/30`. It
validates the `output.csv` already on disk; it does not regenerate it, so run
`main.py` first if you changed anything. The individual checks:

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

## 1.5 Offline rules fallback

```bash
python code/main.py --provider stub
```

No model at all — pure heuristics. It is a floor that always produces a
submittable file, not a peer of the shipping path.

Accuracy against the 30 labelled sample rows:

| path | action | message_type |
|---|---|---|
| `--provider stub` (rules only) | 70% | 47% |
| `--provider nvidia` (shipping) | **93%** | **83%** |

Both paths use the same deterministic safety gate; only the personalization
stage differs. See [§2.2](#22-stage-1--safety-gate-blind).

## 1.6 Configuration

All secrets come from the environment or a `.env` placed beside `code/`. None
is required to reproduce the shipped results — see [§1.3](#13-run-it).
Variables are read at startup; an already-exported value always wins over the
file.

| Variable | Used by |
|---|---|
| `ROUTER_PROVIDER` | `stub` \| `anthropic` \| `nvidia` — personalization engine |
| `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_MODEL` | NIM personalization |
| `ANTHROPIC_API_KEY` | alternative personalization provider |
| `GEMINI_API_KEY`, `GROQ_API_KEY` | media extraction only; not needed to run the router |
| `SAFETY_PROVIDER` | defaults to `stub`; see [§2.2](#22-stage-1--safety-gate-blind) |

---

# 2. Approach

## 2.1 Overview

Three stages. Each message passes through them in order.

```text
messages.csv ─┐
              ├─> [context assembly] ─> [1 SAFETY GATE] ─> [2 PERSONALIZATION] ─> [3 WRITER] ─> output.csv
media.json  ──┘                          (blind, rules)      (full context)         + validator
```

The organising idea is that **risk and preference are different questions and
must not be answered by the same reader.** Stage 1 decides whether a message is
dangerous, and is structurally prevented from seeing how much the user likes the
sender. Only what survives that gate reaches stage 2, which is where taste,
history and timing apply. Stage 3 turns the decision into the required row and
re-checks it from disk the way a grader would.

## 2.2 Stage 1 — safety gate (blind)

`safety.py` decides risk, and can force `mute` with `scam`/`spam` on its own. It
is *blind*: it sees message content plus structural sender facts (verification
status, official vs. used domain, account age, report counts) but is
structurally prevented from seeing the user's engagement history.
`SafetyContext` whitelists every permitted field and `assert_blind()` fails
loudly if an engagement field ever reaches a prompt.

The reason is that the spec requires risk to be muted *"regardless of the user's
usual engagement"*. Ordering alone does not achieve that — a stage that can see
"this user replies to this sender constantly" can rationalise its way out of a
correct flag. Withholding the context is what enforces the rule.

**Why the gate ignores `--provider`.** The gate always runs the deterministic
rules even when personalization uses an LLM. Measured over all 110 rows, the LLM
safety classifier force-muted 44 messages against 22 for the rules, and produced
6 false positives on verified, clean-domain senders — muting HDFC Bank for
"vague urgency framing" and a pharmacy for being an "unverified sender". The
gate's contract is that a trusted sender is never falsely muted, so it stays
deterministic. `--safety-provider` exists only to re-measure that.

## 2.3 Stage 2 — personalization

`personalize.py` — for messages that clear the gate, chooses
notify/digest/mute-for-low-value using group mute state and dismissal rates,
promotion consent (`allows_promotions`, `promotions_opted_out_at`), relationship
staleness, quiet hours, and notification load measured against each user's own
baseline. Signals are always computed and are rendered into the LLM prompt, so
choosing a provider cannot bypass this stage.

## 2.4 Stage 3 — writer + validator

`writer.py`, `validate.py` — emits the exact required columns and re-checks the
file from disk the way a grader would.

## 2.5 Evidence

`evidence.py` is a scored retrieval over the user's history, not a filter.
Each candidate is scored on four weighted terms — topical Jaccard similarity
over the message text and any OCR/ASR text (weight **4.0**, the largest),
same-conversation (3.0), whether the recorded outcome in `message_events.csv`
explains the action we chose (2.0), and same conversation type (0.5). Rows
below `MIN_SCORE` are dropped and the column becomes `none`: a wrong citation
is worse than an absent one. Ties break on id, so selection is deterministic.

The outcome term is what makes a citation explanatory. A message from the same
sender that the user ignored does not justify a `notify`; one they replied to
within five minutes does.

## 2.6 Confidence

`confidence.py` derives an internal certainty per row from the action, the
message type, how many evidence ids corroborate it, whether independent
signals agree or conflict, whether the blind gate forced the decision on a
structural fact, and whether the row is decided from truncated media. That
score is mapped monotonically onto a **per-action** band read off the labelled
samples — notify 0.85–0.91, mute 0.81–0.87, digest 0.78–0.84 — and averaged
with the model's self-reported confidence where one is available.

The bands are per-action because the labelled data orders them that way:
interrupting someone is the call the labeller was surest about, deferring is the
hedge and scores lowest. A single global band loses that ordering and lands
every row in the top sixth of the scale. The map also normalises against the
slice of the certainty scale the score actually occupies, rather than all of
[0, 1], which is what recovers the resolution already present in the signal.

It is deliberately **not** a probability — nothing here is fitted against
outcomes, so 0.85 does not mean "85% likely correct".

## 2.7 Media

Image OCR and voice transcription run **once**, offline, into
`code/cache/media.json`, which is committed. The router never calls a media
provider, so runs are reproducible and the submission needs no Gemini or Groq
key. Regenerate with `code/extract_media.py` / `code/extract_audio.py`.

## 2.8 Determinism

Every model response is cached under `code/cache/` keyed by `message_id`.
A rerun replays the cache and produces a byte-identical `output.csv`.
Temperature is 0 everywhere, but that alone is not sufficient across
providers — the cache is what makes the guarantee real.

Caveat: the key is the `message_id` only, so editing `prompts.py` does **not**
invalidate the cache. Delete `code/cache/routing/` after a prompt change or the
old decisions replay silently. Hashing the prompt into the key is the fix, and
it is a known open gap — see [§3](#3-why-the-cache-is-the-artifact) for why it
was not taken.

Evidence selection (`evidence.py`) and confidence (`confidence.py`) are pure
deterministic functions with no model call, so they are identical on the rules
and LLM paths.

---

# 3. Why the cache is the artifact

Worth stating plainly, because it looks like a shortcut and is not.

Committing the model responses is what makes the shipped `output.csv`
reproducible without credentials. It is also load-bearing in a way we only
discovered by trying to remove it: when the cache was invalidated and the run
re-issued, **the same prompt produced different answers**. Sample accuracy moved
across re-runs — 28/30 cached, then 26/30, 27/30, 26/30 on identical input.

The cause is visible in the run logs. The routing model is a reasoning model and
it sometimes buries its JSON inside 16–18k characters of chain-of-thought; when
extraction fails, that row silently falls back to the rules engine (70%/47%
against the shipping path's 93%/83%). Different runs fail on different rows. The
committed cache contains zero such rows; one re-run contained two.

Two consequences we would rather state than have inferred:

- `temperature=0` is not determinism here. The cache is what makes the
  reproducibility guarantee real.
- Automatic cache invalidation is the correct fix for the caveat above, and it
  is in direct tension with shipping a committed cache — invalidating re-rolls
  all 114 verified responses on a model that does not reproduce. With the
  deadline in hours we chose the validated artifact over the correct mechanism,
  and the accuracy figures quoted here are one measured sample rather than a
  stable property.

---

# 4. Known limitations

Recorded honestly rather than omitted.

- **Three of the five scored criteria are unmeasured locally.** There is no
  ground truth for `reason` quality, evidence relevance, or confidence
  calibration. `score_samples.py` scores only `action` and `message_type`, and
  only on 30 rows. Everything we report about the other three is a proxy.
- **Several constants are inferred from 30 labelled rows, not fitted.** The
  per-action confidence bands (notify 0.85–0.91, mute 0.81–0.87, digest
  0.78–0.84, all inside the 0.78–0.91 observed overall), `evidence.MIN_SCORE`
  with its retrieval weights, and `SECOND_MIN_SIMILARITY` — the threshold a
  second citation must clear. That last one is the most load-bearing and sits on
  a slope rather than a plateau: 0.10 would emit two ids on 41 rows, 0.20 on 14,
  0.30 on 8. It is anchored on a measured property of our own retrieval (the
  median top-pick Jaccard, 0.214) because there is no ground truth for evidence
  quality to fit against. Splitting one confidence band into three makes the
  evidence behind each thinner still. If the hidden truth orders the actions
  differently or wants longer evidence lists, we are wrong in a structured way
  rather than a random one.
- **`digest` confidence is compressed.** All 24 digest rows land on just two
  values (0.80 ×15, 0.79 ×9), spanning 0.79–0.80 against a labelled digest range
  of 0.78–0.84. Those rows' internal certainty genuinely clusters in a narrow
  slice, so spreading them further would mean fitting each action to this run's
  own extremes. Overall spread is 0.028 against the labelled 0.032.
- **The offline rules fallback is materially weaker than the shipping path**
  — 70%/47% against 93%/83%. It is a floor that guarantees a valid file, not
  an equivalent alternative.
- **`event` is under-emitted.** 4 of 110 shipped rows (3.6%) against 4 of 30
  labelled samples (13.3%). The sample misses are `event -> urgent` and
  `event -> business_update`, so the boundary is genuinely soft, but the
  direction of the error is consistent.
- **`spam` is never emitted — a resolved-negative, not an oversight.** The
  discriminator was derived from the labelled samples: `sample_msg_043` is
  `spam` from an unverified sender with 23 reports; `sample_msg_015` is
  `promotion` from a verified sender with 6. So `spam` requires sender
  disrepute, not merely unwanted marketing. Neither discriminator fires on the
  test set — of the 23 gate-cleared business rows exactly one is unverified and
  it has 0 reports — and every heavily-reported unverified sender is already
  gate-muted as `scam`. A `spam` rule would have no trigger here, so we did not
  fit one to a single labelled example.
- **Two voice transcripts begin mid-sentence** (`vn_007`, `vn_014`) — the ASR
  dropped the opening audio. `media_cache.py` flags them and `confidence.py`
  penalises the two affected rows, but they still route on partial content.
  The detector is a heuristic (lowercase first character), so a transcript
  legitimately starting lowercase would be falsely flagged.
- **Risk no longer has exactly one owner.** The deterministic gate force-mutes
  22 rows, and LLM personalization independently labels `scam` on 7 more —
  all group messages with no business record, precisely where the structural
  gate is blind. These look like genuine catches, but the second net is
  undocumented in the architecture above.

---

# 5. File map

| Path | Role |
|---|---|
| `main.py` | entry point; wires the three stages |
| `contracts.py` | shared types, allowed enums, output column order |
| `data.py`, `media_cache.py` | load the 12 CSVs and the media cache |
| `safety.py` | stage 1 — blind safety gate |
| `personalize.py` | stage 2 — personalization signals and rules |
| `router.py`, `prompts.py` | LLM personalization + prompt construction |
| `evidence.py` | scored retrieval for `evidence_message_ids` |
| `confidence.py` | confidence calibration |
| `edge_cases.py` | edge-case assertions on how decisions were produced |
| `writer.py`, `validate.py` | stage 3 — output and contract validation |
| `net.py` | HTTP with retry/backoff for transient provider failures |
| `gate_m2.py`, `score_samples.py`, `eval_harness.py` | verification and self-evaluation |
| `extract_media.py`, `extract_audio.py` | one-off media extraction (M0) |
| `cache/` | committed media extraction + model response cache |
