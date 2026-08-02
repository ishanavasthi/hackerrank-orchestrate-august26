# NEXT.md — ordered work plan

All milestones (M0–M5) are complete, plus the P0 quality pass and the evidence
rework. `python code/eval_harness.py --provider nvidia` is green: contract PASS,
M2 gate PASS, M5 edges PASS, smoke 93% action / 83% type.

This file is written to survive conversation compaction. Each item is
self-contained: what, why it matters, how to know it is done. Work top-down —
the ordering is by risk to the *submission*, not by effort.

State at time of writing: `output.csv` = 110 rows,
`md5 b0dabbce3443bb13f50c4a6afc77cb03`, shipping path = rules safety gate +
NVIDIA NIM personalization.

**Read `CHECKLIST.md` §8 before touching any prompt.** It records that the model
is not reproducible across re-calls and that the committed cache is the artifact,
not an optimisation. That finding invalidates the obvious approach to items 2
and 3 below.

---

## P0 — The only thing that actually blocks submission

### 1. Build the submission bundle from an allowlist, then grep two paths
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

*Done when:* all four hold —
1. the zip is unpacked to a temp dir and a grep for `sk-ant-|nvapi-|gsk_|AIza`
   and for a file named `.env` both return nothing;
2. **the same grep is run against `log.txt`** — it is a shipped artifact that
   records verbatim user prompts and lives outside the repo, so the allowlist
   structurally cannot protect it (last checked: 0 hits);
3. `python code/main.py --provider nvidia` inside the unpacked zip reports
   **`media extracted for 23/23`** and reproduces `output.csv` byte-identically
   **with no API key** — the routing cache is committed, so this works offline;
4. `python code/eval_harness.py --provider nvidia` passes inside the unpacked
   zip.

Conditions 3 and 4 are not formalities. `load_media_cache` is documented *"Never
raises"* (`code/media_cache.py:24`) and returns empty on a missing file — so an
allowlist that forgets `code/cache/` produces 110 perfectly valid rows with all
23 media-bearing messages blind, with no error anywhere. Assert the 23/23 and
the md5, not just that a file appeared.

---

## P1 — Real quality gaps. Only worth starting with hours, not minutes, to spare.

### 2. JSON extraction is not robust to chain-of-thought — this is a root cause
`nvidia/nemotron-3-super-120b-a12b` is a reasoning model and sometimes buries
its JSON in 16–18k characters of reasoning. `router._extract_json` fails, and
`_route_llm` silently drops that row to the rules engine (70%/47%). Measured
during the §8 experiment: one re-run had 2 such rows; the committed cache has 0.

This is the highest-value remaining fix because it is upstream of everything
else. It is *why* re-runs are noisy, so it must be fixed before any prompt change
can be evaluated honestly, and it removes a silent-degradation path in the
shipped pipeline.

*Fix:* extract the last balanced `{...}` block rather than the first; raise
`max_tokens`; and consider requesting a JSON-only response format if NIM
supports one for this model.

*Done when:* a full run reports 0 unparseable replies, and three consecutive
full re-runs produce the same sample score.

### 3. `event` is under-emitted — open, and the naive fix is disproved
We emit `event` on 4/110 rows (3.6%) against 13.3% in the labelled set. Both of
our `message_type` misses are in that direction. `personalize.classify_type`
calls 12 rows `event`; the LLM overrides 8.

A prompt fix was attempted and rolled back — see `CHECKLIST.md` §8. It failed
its pre-registered gate, and more importantly the ±2-row run-to-run noise on 30
samples is larger than the effect being measured.

*Do not retry this before item 2 is done.* Without a known noise floor you
cannot tell an improvement from a lucky run.

*Fix, in order:* item 2 → establish the noise floor over ≥3 runs per variant →
then a taxonomy clarification in `SYSTEM_PROMPT`, judged against that floor.

*Done when:* message_type ≥ 26/30 and action ≥ 28/30, sustained across three
runs rather than observed once.

### 4. Cache invalidation is manual (the long-standing D1)
Implemented this round and **reverted**; the patch is preserved at
`scratchpad/p1/router_cache_keying.patch`. It is correct engineering that is
wrong for this deadline: invalidating forces a re-call of all 114 responses on a
model that does not reproduce.

*Only reopen alongside item 2.* Once re-calls are stable, this becomes safe and
should land.

---

## P2 — Hygiene. No measurable effect on score; do only if time remains.

5. **`code/evaluation/main.py`** is an empty committed organizer scaffold (§7 D7) — delete it or exclude it from the bundle. In a submitted zip an empty `main.py` inside a folder named `evaluation` reads as an abandoned deliverable.
6. **`eval_harness.py` does not regenerate the artifact** (§7 F4) — add an optional `--run`.
7. **`code/safety.py:567`** computes a `SafetyVerdict.confidence` that nothing reads; `main.py` calls `calibrate(..., gate_forced=True)` and ignores it. Now doubly misleading, since 0.88 is outside what the mute band can emit. Delete the field or wire it up.
8. **`_fit()`'s final fallback** (`code/safety.py:451`) returns `head + tail + '.'` with no length check, so it can exceed `_REASON_LIMIT`. It does not fire on this dataset (longest emitted reason is 155 chars).
9. **Two `.env` parsers with different semantics** — `main.py:46` strips trailing comments, `router.py:73` does not. The `.env.example` line that triggered it has been reformatted, so nothing currently mis-parses, but the inconsistency is still there.
10. **LLM calls are sequential** (§7 D2). Bounded concurrency is safe (every call is independent and cached) but only affects re-runs, not the shipped file.

---

## Do NOT reopen

All four were investigated and closed on evidence. Re-litigating them costs
time and would make the output worse.

- **`spam` emission** — resolved-negative. The boundary is sender disrepute
  (unverified + 23 reports), not unwanted marketing; the test set has zero
  triggers on either discriminator. See `DECISIONS.md`.
- **Text/image brand mismatch as a scam signal** — disproved. The dataset
  recycles stock imagery across unrelated senders; `img_010` is labelled
  `mute`/`promotion` under a different sender in the sample set.
- **Does the code run outside this working directory?** — tested and passes.
  `git archive HEAD` into a clean temp dir with `.env` absent and all four key
  env vars unset reproduced `output.csv` byte-identically on the **nvidia**
  path — not just the stub path — because the routing cache is committed.
- **Re-running the LLM to "refresh" the cache** — actively harmful. See §8. The
  committed cache is the validated artifact; re-calling re-rolls the dice and
  measured worse every time (28/30 → 24, 26, 27, 26).

---

## Standing verification

Run before and after every change:

```bash
python code/eval_harness.py --provider nvidia    # everything, one exit code
python code/main.py --provider nvidia && md5 -q output.csv   # determinism
```

`CHECKLIST.md` §7 is the full trade-off backlog (28+ items, tagged GAP /
ACCEPTED / BLIND) and §8 records the re-run experiment. This file is only the
subset that warrants action now.
