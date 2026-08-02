# NEXT.md — ordered work plan

All milestones (M0–M5) are complete. `python code/eval_harness.py --provider nvidia`
is green: contract PASS, M2 gate PASS, M5 edges PASS, smoke 93% action / 83% type.

This file is written to survive conversation compaction. Each item is
self-contained: what, why it matters, how to know it is done. Work top-down —
the ordering is by risk to the *quality of the answer*, not by effort.
Packaging is deliberately last; it is a terminal step and nothing depends on it.

State at time of writing: commit `6150e88`, `output.csv` = 110 rows,
`md5 46ce2fa795a50830a854c599b71ebcd9`, shipping path = rules safety gate +
NVIDIA NIM personalization.

---

## P0 — The silent-failure class. This is the bug family that has bitten us most.

### 1. Cache invalidation is manual across three caches
`code/cache/{media,routing,safety}` are keyed by `message_id` / `media_id`
only. Edit a prompt and the old decisions replay silently — the same shape as
the M5 bug where an error handler's output shipped as a real judgement, and
the M1 bug where a field-name mismatch silently emptied 13 transcripts.

This is P0 because it does not corrupt today's output — it makes *tomorrow's
fixes invisible*. Every item below this line is a change to prompts or scoring,
and each one risks being verified against a replayed cache rather than a fresh
call. Fix this first or the rest of the list cannot be trusted.

*Fix:* include a hash of (system prompt + user prompt) in the cache key or as a
stored field checked on read; on mismatch, re-call rather than replay.

*Done when:* changing one character of `SYSTEM_PROMPT` causes a re-call instead
of a cache hit, demonstrated in a test.

---

## P1 — Scoring criteria we have never actually examined

### 2. `reason` quality has never been systematically reviewed
It is **1 of the 5 stated scoring criteria** and we have only ever glanced at
individual examples. There is no ground truth, so this is a manual read, not a
metric.

*Fix:* dump all 110 reasons, read them as a set, and look for: template
repetition, reasons that restate the action without justifying it, reasons
citing evidence we did not actually use, and the 22 gate-forced rows whose
reason is a machine-generated signal list rather than prose.

*Done when:* every reason has been read once and the weak classes are either
fixed or recorded in §7.

### 3. The confidence band compresses our signal, and inverts on gated rows (§7 F1)
Internal certainty has real resolution, but the shipped band is only **0.87–0.91
across 5 distinct values** on 110 rows (measured; the 0.78–0.91 figure quoted
earlier was the *sample* range, not ours), so a deliberate 0.15 penalty moves
the number by ~0.01.

Worse, the band is **inverted where it matters**. The 22 rows the safety gate
force-mutes never reach the model at all — there is no routing-cache entry for
them — yet they carry **0.89–0.91**, the highest values in the file, while every
genuinely reasoned row sits at 0.88. Confidence is highest exactly where the
least classification happened.

*Fix:* decide explicitly — either widen the band and make gate-forced rows score
below reasoned ones, or keep it and record that our confidence is near-constant
by construction.

*Done when:* the choice is made and written into `DECISIONS.md`, not left implicit.

---

## P2 — Decisions to finalise rather than leave half-stated

### 4. Rules fallback is much weaker than the shipping path (§7 A2)
70%/47% versus 93%/83%. A no-key run still produces a valid `output.csv`, just a
materially worse one. This is currently an unstated asymmetry.

*Fix:* either state it plainly in `code/README.md` as a degraded-but-valid
fallback, or close part of the gap (the LLM's `message_type` discrimination is
the bigger half).

### 5. "Risk has exactly one owner" is no longer true (§7 A4)
LLM personalization labels `scam` on 7 gate-cleared rows — all group messages
with no business record, where the structural gate is blind. The safety property
still holds end-to-end (0 of 23 trusted senders mislabelled), but the
`DECISIONS.md` entry asserting single ownership is now inaccurate.

*Fix:* rewrite that entry to describe what the system actually does.

---

## P3 — Hygiene. No measurable effect; do only if time remains.

6. **`code/evaluation/main.py`** is an empty committed organizer scaffold (§7 D7) — delete or use it.
7. **`eval_harness.py` does not regenerate the artifact** (§7 F4) — add an optional `--run`.
8. **LLM calls are sequential** (§7 D2); a full run takes several minutes. Bounded concurrency is safe (every call is independent and cached) but only affects re-runs, not the shipped file.

---

## Last step — packaging. Do this only when everything above is closed.

### 9. Build the submission bundle from an allowlist, then grep two paths
The README requires three artifacts: **code zip**, **`output.csv`**, **chat
transcript** (`~/hackerrank_orchestrate_august26/log.txt`).

`.env` stays where it is — at the repo root, gitignored, alongside the tracked
`.env.example`. That is the convention and it keeps `cp .env.example .env`
working for anyone who clones the repo. The git vector is already closed:
`.env` is untracked and a `git grep` for `sk-ant-|nvapi-|gsk_|AIza` over all
tracked files returns nothing. The only live vector is a **directory sweep**
(`zip -r code.zip .`), which is a property of how the bundle is built, not of
where the file lives.

*Fix:* build the zip from an explicit allowlist, never a directory sweep.

*Done when:* all three hold —
1. the zip is unpacked to a temp dir and a grep for `sk-ant-|nvapi-|gsk_|AIza`
   and for a file named `.env` both return nothing;
2. **the same grep is run against `log.txt`** — it is a shipped artifact that
   records verbatim user prompts and lives outside the repo, so the allowlist
   structurally cannot protect it (last checked: 0 hits across 33 entries);
3. `python code/main.py --provider stub` inside the unpacked zip reports
   **`media extracted for 23/23`** and writes a valid 110-row `output.csv`.

Condition 3 is not a formality. `load_media_cache` is documented *"Never
raises"* (`code/media_cache.py:24`) and returns empty on a missing file — so an
allowlist that forgets `code/cache/` produces 110 perfectly valid rows with all
23 media-bearing messages blind, with no error anywhere. Assert the 23/23, not
just that a file appeared.

---

## Do NOT reopen

All three were investigated and closed on evidence. Re-litigating them costs
time and would make the output worse.

- **`spam` emission** — resolved-negative. The boundary is sender disrepute
  (unverified + 23 reports), not unwanted marketing; the test set has zero
  triggers on either discriminator. See `DECISIONS.md`.
- **Text/image brand mismatch as a scam signal** — disproved. The dataset
  recycles stock imagery across unrelated senders; `img_010` is labelled
  `mute`/`promotion` under a different sender in the sample set.
- **Does the code run outside this working directory?** — tested and passes.
  `git archive HEAD` into a clean temp dir with `.env` absent and all four key
  env vars unset ran end-to-end on the stub path: 110 messages, 23/23 media,
  22/110 gated, output byte-identical to the in-place run
  (`md5 c27ea4c4cd287376a4c12ed960afbdba`). The caches are tracked (230 files),
  so a clean checkout has everything it needs. There is no portability bug to
  find; what remains is item 9, which is packaging.

---

## Standing verification

Run before and after every change:

```bash
python code/eval_harness.py --provider nvidia    # everything, one exit code
python code/main.py --provider nvidia && md5 -q output.csv   # determinism
```

`CHECKLIST.md` §7 is the full trade-off backlog (28+ items, tagged GAP /
ACCEPTED / BLIND). This file is only the subset that warrants action now.
