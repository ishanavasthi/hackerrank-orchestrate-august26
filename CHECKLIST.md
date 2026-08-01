# CHECKLIST.md

Status of the Message Notification Router. Every claim below was verified by
running the command shown, not asserted from memory. Last verified against
commit `651ba9b` + working tree.

---

## 1. Submission contract (`problem_statement.md` §Output, `README.md` §Submission)

| Requirement | Status | How verified |
|---|---|---|
| `output.csv` exists with exact column order | PASS | `python code/validate.py output.csv` |
| Exactly one row per `message_id` in `messages.csv` (110) | PASS | validator: set equality, no dupes, no extras |
| Every `action` in {notify, digest, mute} | PASS | validator |
| Every `message_type` in the 11 allowed values | PASS | validator |
| `confidence` numeric and within [0,1] | PASS | validator (actual range 0.78–0.89) |
| `evidence_message_ids` resolve in `message_history.csv` | PASS | validator: 0 dangling ids |
| Runnable from the terminal | PASS | `python code/main.py` |
| Reads only from `dataset/` | PASS | no organizer-only files exist in this repo |
| No hardcoded labels | PASS | no sample label is copied to any test row (id namespaces disjoint) |
| Secrets from environment only | PASS | `git grep` for key-shaped strings returns nothing; `.env` untracked |
| Deterministic | PASS | two consecutive runs produce byte-identical `output.csv` |
| **Setup/run instructions in the package** | **MISSING** | §6.3 requires it; `README.md` is the organizer's, not ours |

**Runs with zero API keys.** Verified with every provider variable unset:
the default `stub`/rules path produces a valid `output.csv` offline. This is
the submission's floor — see DECISIONS.md.

---

## 2. Milestones

### M0 — media extraction — DONE
- 33/33 media entries cached in `code/cache/media.json` (20 images, 13 voice), zero errors.
- Images: Gemini (14 on `gemini-3.6-flash`, 6 on `gemini-3.5-flash` after the 20/day quota ran out).
- Voice: Groq `whisper-large-v3-turbo`.
- Cache is committed, so the router never calls a media provider. This is what makes reruns deterministic.

### M1 — end-to-end skeleton — DONE
- 12 CSV loaders, context assembly, writer, standalone validator.
- Built in three parallel Orca worktrees against a frozen `contracts.py`; merged with zero conflicts.
- Gate met: `output.csv` validates.

### M2 — blind safety gate — DONE
- `python code/gate_m2.py` → PASS on all four assertions.
- 8/8 must-mute rows (msg_091 + 7 impersonation-domain business rows).
- 0 false positives across 23 trusted rows.
- Blindness enforced: 21 engagement field names checked across 110 rendered prompts.
- 22/110 force-muted on risk.

### M3 — personalization — DONE
- 88 gate-clearing messages routed on group mute state, dismissal rates, promotion consent, relationship staleness, DND, per-user notification load.
- Spec carve-out implemented and verified (`msg_056` notifies, `msg_040` does not).
- 14/14 hand-checked cases.
- Distribution: notify 28 / digest 36 / mute 46.

### M4 — evidence + confidence — NOT STARTED
- Evidence selection in `personalize._evidence()` is an explicit placeholder: same-conversation history filtered by whether the outcome matches the action. It does not do similarity ranking or outcome-informativeness scoring as specified in DECISIONS.md.
- 23/110 rows currently emit `none` for evidence.
- Confidence is a small lookup table keyed on action, not calibrated.

### M5 — edge cases + eval harness — PARTIAL
- `code/score_samples.py` now exists (added during this audit).
- Edge cases handled incidentally so far: empty `message_text` (`msg_085`), media-only rows, unknown senders, muted-group-with-mention.
- Not yet systematically swept.

---

## 3. Measured quality — the number we did not have until now

`python code/score_samples.py`

```
action        : 21/30 = 70%
message_type  : 14/30 = 47%
```

Read this with the caveat from DECISIONS.md: 30 rows is thin and the sample
action split (9/11/10) is too uniform to be the real class balance. Per-row
correctness is signal; the distribution is not.

**The dominant error is systematic, not random: 6 of 9 action misses are
`notify` → `digest`.** The ground truth interrupts the user considerably more
readily than we do — it notifies for a packed order, a school circular, and
"@u_004 when you get 5 mins can you call? Nothing dramatic" (which our
defusing-language rule explicitly demotes). Our conservatism is costing roughly
20 points of action accuracy and is the highest-value thing to fix in M4/M5.

**`message_type` at 47% is the weaker half** and has had no dedicated work at
all — it has been a by-product of the action rules rather than a target.

---

## 4. Gaps and misses found in earlier work

Ordered by risk to the submission.

1. **No setup/run instructions for our solution.** §6.3 of AGENTS.md and the
   README submission checklist both require them. `README.md` is the
   organizer's file. **This is a submission blocker, not a nicety.**

2. **`spam` is never emitted — and this is now confirmed to cost us.** It was
   logged in DECISIONS.md as a coin-flip on the grader's taxonomy. The sample
   scoring settles it: `sample_msg_043` is labelled `mute`/`spam` and we
   produce `digest`/`promotion`. The ground truth does use `spam` for
   promotional blasts. **Resolve in M5.**

3. **The `anthropic` and `nvidia` provider paths have never executed.** No
   `code/cache/routing/` or `code/cache/safety/` directory exists, which proves
   no live call has ever been made. Both code paths are unexercised and may be
   broken. Everything measured so far is the offline rules path.

4. **Media-driven rows may be weaker than they look.** `code/cache/asr_comparison.md`
   records that three transcripts begin mid-sentence. Those rows route on
   truncated content and nothing currently flags them.

5. **Dead code left in `router.py`.** `INJECTION_PATTERNS` and `_domain_mismatch`
   are now defined but unreferenced after M2 moved risk ownership to
   `safety.py`; `SCAM_KEYWORDS` is nearly so. Harmless at runtime, but it
   invites someone to re-enable the exact duplicate-risk bug we removed.

6. **`code/evaluation/main.py` is an empty organizer scaffold** (0 bytes,
   committed). Either use it or leave it clearly alone; right now it looks
   like an unfinished deliverable.

7. **No `requirements.txt`.** In fact the pipeline has **zero third-party
   dependencies** (stdlib only, `urllib` not `requests`), which is a strength
   — but it needs stating, or a reviewer will assume something is missing.

8. **The urgency-defusing guard is re-derived at three call sites.** Noted in
   DECISIONS.md: I applied it to two and missed the greeting branch on the
   first pass. It should be computed once in `Signals`.

9. **DECISIONS.md was silently overwritten once.** Two DND entries were lost
   when a parallel session rewrote the file from an older base; recovered.
   Worth watching if parallel sessions resume.

---

## 5. Reproduce every claim here

```bash
python code/main.py            # full pipeline + validation gate   -> PASS 110 rows
python code/validate.py output.csv   # standalone grader-style check
python code/gate_m2.py         # M2 safety gate assertions         -> PASS
python code/score_samples.py   # accuracy vs 30 labelled rows      -> 70% / 47%
md5 -q output.csv && python code/main.py >/dev/null && md5 -q output.csv   # determinism
```
