# CHECKLIST.md

Status of the Message Notification Router. Every claim below was verified by
running the command shown, not asserted from memory.

**Maintenance rule:** every non-trivial trade-off, known weakness or accepted
cost goes in §7 as it is made — not at the end. §7 is the polish backlog we
work through once implementation is complete.

**Active work plan:** see [`NEXT.md`](./NEXT.md) for the ordered subset of §7
that warrants action now, ordered by risk to the quality of the answer.
Packaging is the last item there, not the first. Last verified against
commit `HEAD` (`output.csv` md5 `f4fc125c88ae7357b20a32dc5a0f0acb`).

---

## 1. Submission contract (`problem_statement.md` §Output, `README.md` §Submission)

| Requirement | Status | How verified |
|---|---|---|
| `output.csv` exists with exact column order | PASS | `python code/validate.py output.csv` |
| Exactly one row per `message_id` in `messages.csv` (110) | PASS | validator: set equality, no dupes, no extras |
| Every `action` in {notify, digest, mute} | PASS | validator |
| Every `message_type` in the 11 allowed values | PASS | validator |
| `confidence` numeric and within [0,1] | PASS | validator (shipping path emits 0.79–0.89, 11 distinct values, per-action bands) |
| `evidence_message_ids` resolve in `message_history.csv` | PASS | validator: 0 dangling ids |
| Runnable from the terminal | PASS | `python code/main.py` |
| Reads only from `dataset/` | PASS | no organizer-only files exist in this repo |
| No hardcoded labels | PASS | no sample label is copied to any test row (id namespaces disjoint) |
| Secrets from environment only | PASS | `git grep` for key-shaped strings returns nothing; `.env` untracked |
| Deterministic | PASS | two consecutive runs produce byte-identical `output.csv` |
| Setup/run instructions in the package | PASS | `code/README.md`; root README links to it |

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
- 23/110 force-muted on risk (msg_022 added in the pre-submission review — see §9).

### M3 — personalization — DONE (rules + LLM)
- 88 gate-clearing messages routed on group mute state, dismissal rates, promotion consent, relationship staleness, DND, per-user notification load.
- Spec carve-out implemented and verified (`msg_056` notifies, `msg_040` does not).
- 14/14 hand-checked cases.
- Shipping path distribution over 110: notify 34 / digest 24 / mute 52.

### M4 — evidence + confidence — DONE
- `code/evidence.py`: scored retrieval (topical similarity + same-conversation + outcome support). Rows with no evidence 28 -> 3; unrelated-thread 21 -> 8; 0 dangling.
- `code/confidence.py`: certainty from corroboration, signal agreement/conflict and structural grounding, mapped monotonically onto a band.
- **Both were revised after M5 — see §8.** Evidence now emits 0/1/2 ids on 3/93/14 rows (was 3/6/101) because the second citation must earn its place; confidence uses per-action bands and emits 0.79-0.89 across 11 distinct values (was 0.87-0.91 across 5).
- Applied on both paths and to gate-forced mutes (which previously emitted `none` for all 22 rows).
- Runs after the response cache read, so the ranking can be revised without re-calling the API.

### M5 — edge cases + eval harness — DONE
- `code/edge_cases.py`: gate over the four named classes (unknown senders 3, thin history 9, muted-group carve-out 2, borderline scam 18) plus a silent-fallback guard.
- That guard caught a live bug: 2 rows were decided by an error handler, one of them `msg_056`, the spec's own carve-out example, which was shipping as `digest`/`unknown`.
- `code/eval_harness.py`: contract + M2 gate + M5 edges + smoke test behind one exit code.
- `spam` resolved as not-implementable on this dataset (see DECISIONS.md).
- Truncated transcripts now flagged and confidence-penalised.

---

## 3. Measured quality

`python code/score_samples.py [--provider nvidia]`

| path | action | message_type |
|---|---|---|
| rules (offline fallback) | 70% | 47% |
| **rules gate + NIM personalization (shipping)** | **93%** | **83%** |

The LLM corrects the systematic conservatism the rules path showed: 8 of 9
notifies correct against 3 of 9. Caveat unchanged — 30 rows is thin, and per
DECISIONS.md we do not tune against them.

On the full 110 rows the shipping path gives notify 34 / digest 24 / mute 52,
with `unknown` collapsing from 15 to 0 and zero dangling evidence ids.

**One systematic error remains: `event` is under-emitted.** We emit it on 4 of
110 rows (3.6%); the labelled set uses it on 4 of 30 (13.3%). Both of our two
`message_type` misses are in that direction (`event`->`urgent`,
`event`->`business_update`), so they are one pattern, not two one-offs, and the
pattern reproduces on the test set: `personalize.classify_type` calls 12 rows
`event` and the LLM overrides 8 of them. The sharpest case is msg_077 ("School
circular attached for tomorrow's field trip...") which we type `urgent`, against
a near-identical labelled row typed `event`.

We attempted a prompt fix and **rolled it back** — see §8. It is left open
deliberately, not overlooked.

**The remaining misses are one-offs.** The 2 action misses and the other 3
message_type misses are distinct (`personal`->`event`, `spam`->`promotion`,
`unknown`->`personal`). The planned "P3" accuracy pass was defined against the
rules path, where the error WAS systematic (6 of 9 action misses in one
direction, 47% type accuracy). Switching the personalization engine resolved
that, so P3 was dropped rather than executed: chasing scattered one-offs across
30 rows is the per-row fitting DECISIONS.md commits against, and would likely
hurt the hidden set.

---

## 4. Gaps and misses found in earlier work

Ordered by risk to the submission.

1. ~~No setup/run instructions for our solution.~~ **RESOLVED (P2).**
   `code/README.md` covers requirements, quick start, architecture,
   verification, configuration, and known limitations. The root `README.md`
   now links to it; the organizer's content was not modified.

2. ~~`spam` is never emitted.~~ **RESOLVED-NEGATIVE (M5).** The boundary is
   sender disrepute, not unwanted marketing: `sample_msg_043` is `spam` from an
   unverified sender with 23 reports, `sample_msg_015` is `promotion` from a
   verified sender with 6. Neither discriminator has any trigger in the test
   set — 1 of 23 gate-cleared business rows is unverified and it has 0 reports,
   and there are no `ignored_*` relationships at all. **Do not implement.**

3. ~~The provider paths have never executed.~~ **RESOLVED (P1).** Both now
   run. Findings: the LLM *safety* classifier fails the M2 gate (6 false
   positives on 23 trusted senders), so the gate stays deterministic; the LLM
   *personalization* is far better (93%/83% vs 70%/47%) and is now the
   shipping path. Retry/backoff added after a 503 and then a Python-3.9
   `socket.timeout` each killed a full run.

4. ~~Media-driven rows may be weaker than they look.~~ **RESOLVED (M5).**
   2 transcripts begin mid-sentence (not 3 as recorded); `media_cache` now
   flags them and confidence drops on the 2 affected rows.

5. ~~Dead code left in `router.py`.~~ **RESOLVED (P4).** `INJECTION_PATTERNS`
   and `_domain_mismatch` were unreferenced and are removed. The earlier claim
   that `SCAM_KEYWORDS` was "nearly" dead was wrong — it has a live use in
   evidence selection (matching scam-like history when the type is already
   `scam`), which is not risk classification, so it stays.

6. **`code/evaluation/main.py` is an empty organizer scaffold** (0 bytes,
   committed). Either use it or leave it clearly alone; right now it looks
   like an unfinished deliverable.

7. **No `requirements.txt`.** In fact the pipeline has **zero third-party
   dependencies** (stdlib only, `urllib` not `requests`), which is a strength
   — but it needs stating, or a reviewer will assume something is missing.

8. ~~The urgency-defusing guard is re-derived at three call sites.~~
   **RESOLVED (P4), and it was worse than recorded.** Nine decision branches
   read the raw `s.urgent` keyword hit instead of the defused value — so a
   muted-group message saying "Nothing urgent" was digested rather than muted.
   Now computed once as `Signals.really_urgent`; the raw flag is marked
   do-not-branch-on. Latent only: no sample row exercises it, so scores are
   unchanged (14/14 hand cases, gate PASS, 70%/47% rules, output.csv
   byte-identical).

9. **DECISIONS.md was silently overwritten once.** Two DND entries were lost
   when a parallel session rewrote the file from an older base; recovered.
   Worth watching if parallel sessions resume.

10. **Risk no longer has exactly one owner — and the DECISIONS.md entry saying
    it does is now inaccurate.** The LLM personalization stage labelled 7
    gate-cleared rows as `scam` (QR-payment demands, phishing links, a
    prompt-injection attempt). All 7 are group messages with no business
    record — precisely where the structural gate is blind — so these look like
    genuine gate misses the LLM caught, not false positives. The safety
    property still holds end-to-end: **0 of 23 trusted senders are labelled
    scam/spam by any stage.** But the clean ownership story is no longer
    literally true and should be restated rather than defended.

11. **Confidence is out of band on the LLM path.** Two rows come back at 0.50
    against a 0.78 sample floor — one of them `msg_056`, the spec carve-out
    case. Confidence calibration is M4; this is the concrete evidence for it.

---

## 5. Verified end-to-end assertions

```
110 rows, exact columns, all enums valid, 0 dangling evidence ids
0 of 23 trusted senders labelled scam/spam by any stage
M2 gate: 8/8 must-mute, 0/23 false positives, blindness over 110 prompts
determinism: rerun replays cache byte-identically
offline: runs with every provider key unset
```

## 6. Reproduce every claim here

```bash
python code/main.py --provider nvidia    # shipping path  -> PASS 110 rows
python code/main.py --provider stub      # offline fallback, no keys needed
python code/validate.py output.csv       # standalone grader-style check
python code/gate_m2.py                   # M2 gate assertions -> PASS
python code/score_samples.py                     # rules   -> 70% / 47%
python code/score_samples.py --provider nvidia   # shipping -> 93% / 83%
python code/edge_cases.py                # M5 edge-case gate  -> PASS
python code/eval_harness.py --provider nvidia    # everything, one exit code
md5 -q output.csv && python code/main.py --provider nvidia >/dev/null && md5 -q output.csv
```

12. **Text/image brand mismatch — investigated, disproved, closed.** The LLM
    safety gate flagged msg_049 (Shopee sender, JioMart image) and msg_066
    (Target sender, Amazon image), and I twice recorded this as a real signal
    worth implementing in the rules gate. It is not. `img_010` is used by
    Myntra in `sample_msg_047`, ground truth `mute`/`promotion`, and reused by
    Target in msg_065/msg_066; the only other brand-mismatched sample,
    `sample_msg_048`, is `digest`/`business_update`. The dataset recycles stock
    imagery across unrelated senders, so mismatch is a construction artifact.
    Building the rule would have pushed msg_066 to `scam` against the only
    labelled example of that image. **Do not implement. Do not revisit in M5.**

---

## 7. Shortcomings & trade-offs — polish backlog

Maintained from here on: every non-trivial trade-off, known weakness, and
accepted cost, in one place, to be revisited once implementation is complete.
Sourced from all 34 trade-off notes in `DECISIONS.md` plus findings that only
surfaced in verification.

Legend — **[GAP]** something we would fix given time; **[ACCEPTED]** a
deliberate cost we would likely choose again; **[BLIND]** we cannot currently
tell whether it is a problem.

### A. Accuracy and correctness

| # | Item | Why it exists | What fixing looks like |
|---|---|---|---|
| A1 | ~~`spam` is never emitted~~ **RESOLVED-NEGATIVE (M5)** | Boundary is sender disrepute, not unwanted marketing | **Do not implement** — zero triggers in the test set on either discriminator |
| A2 | **[GAP]** Rules fallback is materially weaker than the shipping path (70%/47% vs 93%/83%) | It is a heuristic floor, not a peer | Either accept it as a floor explicitly, or port the LLM's better `message_type` discrimination into rules |
| A3 | ~~Voice transcripts begin mid-sentence, unflagged~~ **RESOLVED (M5)** | 2 transcripts (not 3) lost opening audio | Done: `media_cache` flags them, confidence penalised on the 2 affected rows |
| A4 | **[GAP]** Risk no longer has exactly one owner | LLM personalization labels `scam` on 7 gate-cleared rows (all groups with no business record) | Either accept it as a documented second net, or move those detections into the deterministic gate |
| A5 | **[ACCEPTED]** Safety gate is biased toward false positives | Blindness is what enforces "muted regardless of usual engagement"; it cannot use a long trust relationship to clear a message | Only revisit if false mutes are observed on real trusted senders — currently 0 of 23 |
| A6 | **[ACCEPTED]** Urgency requires an explicit anchor; unanchored urgent phrasing is missed | Bare `now`/`today` produced false positives on "Smile today" | Broaden anchors only with evidence; the loose version was measurably worse |
| A7 | **[ACCEPTED]** DND rule ships on reasoning, not evidence | The data showed no suppression effect; the rule is near-inert on this set | Revisit only if the hidden set has notify-worthy messages inside quiet hours |

### B. Values inferred from thin evidence

Every item here is tuned against 30 labelled rows or fewer. None is fitted.

| # | Item | Basis | Risk if wrong |
|---|---|---|---|
| B1 | **[BLIND]** Per-action confidence bands (notify 0.85–0.91, mute 0.81–0.87, digest 0.78–0.84) | Per-action min/max of 30 sample rows — thinner evidence than the single band it replaced | If the hidden truth orders the actions differently, we are wrong in a structured rather than random way |
| B2 | **[BLIND]** `SECOND_MIN_SIMILARITY = 0.20`, the threshold a second citation must clear | Anchored on our own measured median top-pick Jaccard (0.214), not fitted — there is no ground truth for evidence quality (C1) | It is on a slope, not a plateau: 0.10 -> 41 two-id rows, 0.20 -> 14, 0.30 -> 8. If the hidden labels want longer evidence lists, this is the single number to move |
| B3 | **[BLIND]** `MIN_SCORE` evidence threshold, and the similarity/conversation/outcome weights | Judgement, not fitted | Too high suppresses good citations; too low cites noise |
| B4 | **[BLIND]** Impersonation thresholds (180d account, 60d domain, 15 reports) | Separate cleanly on 12 mismatching rows | An attacker aging a domain past them clears the gate |
| B5 | **[BLIND]** Negation detection is regex-level, covered by 9 unit cases | Built after the FedEx "no OTP required" false positive | Unanticipated phrasings mishandled in either direction |

### C. Measurement blind spots

| # | Item | Consequence |
|---|---|---|
| C1 | **[BLIND]** No ground truth for **evidence quality** — 1 of 5 scoring criteria | All M4 evidence numbers are proxies (same-conversation rate, resolution rate), not correctness |
| C2 | **[BLIND]** No ground truth for **`reason` quality** — 1 of 5 scoring criteria | We know LLM prose reads better than rule templates; we cannot score it |
| C3 | **[BLIND]** No ground truth for **confidence calibration** — 1 of 5 criteria | Nothing is fitted against outcomes; 0.85 does not mean "85% likely correct" |
| C4 | **[BLIND]** `score_samples.py` measures only `action` and `message_type`, on 30 rows | Three of five scoring criteria are unmeasured locally |
| C5 | **[BLIND]** Personalization cannot be scored independently of the safety gate | A gate regression and a personalization regression look identical in the score |

### D. Operational and structural

| # | Item | Why it exists | What fixing looks like |
|---|---|---|---|
| D1 | **[GAP, DELIBERATELY NOT FIXED]** Cache invalidation is manual across three caches (media, routing, safety) | Caching is what makes determinism real | Implemented (prompt hash stored in the payload, checked on read) and then **reverted** — see §8. It is correct engineering that is wrong for this deadline: invalidating forces a re-call of all 114 responses, and the model is not reproducible. Patch preserved at `scratchpad/p1/router_cache_keying.patch` |
| D2 | **[GAP]** LLM calls are sequential; a full 110-row run takes several minutes | Simplicity | Bounded concurrency — safe because every call is independent and cached |
| D3 | **[ACCEPTED]** Headline result depends on a provider and its quota | The hybrid is +23/+36 points over rules | Mitigated: cache makes reruns free, and the offline path still ships a valid file |
| D4 | **[ACCEPTED]** Three providers means three SDKs, quotas and accounts to reproduce | Quota independence per modality | Would consolidate only if a single vendor covered ASR + OCR + routing well |
| D5 | **[ACCEPTED]** Committed media cache makes the repo less obviously "run from scratch" | Reproducibility, and Gemini's 20/day free quota makes re-extraction expensive | Documented in `code/README.md`; regeneration scripts are included |
| D6 | **[ACCEPTED]** No self-consistency sampling | It directly contradicts the determinism commitment | Would need a fixed-seed majority vote, cached — probably not worth it |
| D7 | **[GAP]** `code/evaluation/main.py` is an empty committed organizer scaffold | Inherited | Delete it or put the eval harness there |
| D8 | **[ACCEPTED]** Stdlib-only, so no embeddings, no pandas, no dotenv library | Zero-install reproducibility | Only revisit if a dependency buys measurable accuracy |

### F. New in M5

| # | Item | Why it exists | What fixing looks like |
|---|---|---|---|
| F1 | ~~The 0.78–0.91 band compresses our signal~~ **RESOLVED (§8)** | The map also normalised against all of [0,1] when certainty only occupies ~0.60–0.89 | Done: per-action bands + renormalisation. stdev 0.012 -> 0.028, distinct 5 -> 11, ordering now matches the labelled set. Residual: `digest` still spans only 0.79–0.80 |
| F2 | **[BLIND]** Field salvage may accept a partial model reply the model would have revised | Recovering a truncated answer beats discarding it | Only detectable if a salvaged row is later found wrong |
| F3 | **[BLIND]** Truncation detector is a heuristic (lowercase first character) | No truncation flag from the ASR provider | A transcript legitimately starting lowercase is falsely flagged; costs 0.01 confidence |
| F4 | **[GAP]** `eval_harness.py` validates the artifact on disk, it does not regenerate it | Keeps it fast and provider-agnostic | Add an optional `--run` that regenerates before checking |
| F5 | **[ACCEPTED]** `spam` is never emitted | Resolved-negative: no trigger exists in the test set | Revisit only if the hidden labels prove to use a different criterion |

### E. Process notes worth keeping

| # | Item |
|---|---|
| E1 | A model's **cited facts can be true while its inference is wrong** — the brand-mismatch episode. Verify the conclusion, not just the evidence |
| E2 | The same **negation trap** appeared three times (safety `no OTP required`, urgency `Nothing urgent`, greeting branch). Any new keyword family should be assumed to need a negation guard |
| E3 | A **flag re-derived at several call sites will drift** — 9 branches read the raw urgency flag before consolidation |
| E4 | **Verification scripts need verifying too** — a determinism check compared stub output against LLM output and reported a false failure; an exit-code test read `tail`'s status instead of the harness's |
| E6 | **Every individual check can pass while the system is wrong.** Two rows were decided by an error handler with the contract validator, the safety gate and the row count all green. Assert on *how* a decision was produced, not only on its shape |
| E5 | `DECISIONS.md` was **silently overwritten** by a parallel session once; two entries were lost and recovered |


---

## 8. The re-run experiment — what it cost and what it taught

Run against commit `f5b36e2`, reverted at `8daebd7`. This is the most important
finding in this file and it was not visible until we tried to change a prompt.

### What we set out to do

Fix the `event` under-emission (§3) by adding taxonomy guidance to
`SYSTEM_PROMPT`. Because the routing cache keys on `message_id` only, a prompt
edit would have replayed stale decisions and looked like a no-op — so the
enabling step was D1: hash the prompt into the cached payload and re-call on
mismatch. That part worked, and is preserved as a patch.

### What actually happened

Invalidating the cache forced genuine re-calls, and the scores moved:

| run | action | message_type |
|---|---|---|
| committed baseline (cached) | **28/30** | 25/30 |
| variant C | 24/30 | 24/30 |
| **prompt reverted to baseline** | **26/30** | 24/30 |
| variant D | 27/30 | 25/30 |
| variant E | 26/30 | 26/30 |

Read the third row. That is the *original prompt*, restored — scoring 26/30
instead of 28/30. **The prompt was never the variable.**

### The finding

**The model is not reproducible across re-calls, and the committed cache was
concealing it.** Action scores bounce 24 / 26 / 27 / 26 on re-runs of
substantially the same input: that is ±2 of noise on a 30-row sample, which is
larger than any effect we were trying to measure.

The mechanism is in the run logs. `nvidia/nemotron-3-super-120b-a12b` is a
reasoning model, and it sometimes buries its JSON inside 16–18k characters of
chain-of-thought:

```
retrying msg_009: reply had no readable JSON (18744 chars) [attempt 2/2]
```

When extraction fails, `router._route_llm` falls back to the rules engine
(70%/47%) and marks the row. Different runs fail on different rows. The
committed cache has **0** such rows; one re-run had 2.

### Consequences, stated plainly

1. **Our headline 93%/83% is one sample, not a stable property.** It is a real
   measurement of the artifact we are shipping, and every check in this file
   was run against exactly that artifact. But a re-run would not reproduce it,
   and the same variance applies to the 110 hidden-set predictions.
2. **`temperature 0` is not determinism.** DECISIONS.md already said the cache
   is what makes the guarantee real. That is more literally true than intended:
   the cache is not an optimisation, it is the artifact.
3. **D1 and the committed-cache reproducibility story are in direct tension.**
   Automatic invalidation is correct, and it would re-roll 114 hand-verified
   responses on a non-deterministic model every time a prompt is touched. We
   chose the validated artifact over the correct mechanism, with hours left.
4. **`event` (§3) stays open.** The best variant reached 26/30 message_type but
   only 26/30 action, failing the pre-registered gate (action ≥ 28 *and* type
   ≥ 26). We could not distinguish a real improvement from run-to-run noise on
   30 rows, so we shipped the validated artifact.

### If there were more time

Make JSON extraction robust to chain-of-thought (the single highest-value fix —
it is the root cause, and it also removes silent rules-fallback rows), then
re-measure with several runs per variant so the noise floor is known before any
prompt change is judged against it.

### Process note

The gate was pre-registered *before* the experiment ran, with an explicit
rollback command. That is the only reason this reads as a finding rather than
as a 2-point regression we talked ourselves into shipping.

---

## 9. The msg_022 phish — found in pre-submission review

A real defect, found by adversarial review after every check in this file was
already green. Worth recording because the green checks are exactly why it
survived so long.

### The defect

`msg_022` shipped as **notify / payment / 0.87** — we were interrupting the user
with a payment-link phish. The dataset contains a designed minimal pair:

| | text | correct |
|---|---|---|
| `msg_021` (u_043, admin) | "Payment due today. Complete before 5 PM… **Please don't use any payment link shared by residents.**" | notify |
| `msg_022` (u_044, member) | "Payment due today. Complete before 5 PM… **Use this link and send screenshot here so I can update it faster.**" | mute |

Identical first two sentences. One warns against payment links; the other
supplies one. Corroboration the blind gate cannot see, but which confirms the
label: `u_044` is a `member`, not an admin; the standing official channel is
stated repeatedly by the real admin ("pay only in the society app or at the
office QR"); `u_044`'s only two priors with this user are a payment link and an
off-notice QR, and `message_events.csv` records `notification_dismissed=1` **and**
`muted_after_message=1` on both.

### Why the gate missed it

`content_risk` returned only a lure (`send screenshot`) and no coercion, and the
force-mute condition is `cred or inj or (coer and lure)`.

### The fix, and the more useful negative result

One pattern added to `_COERCION`: `\bbefore \d{1,2}(?::\d{2})? ?(?:am|pm)\b`.

The obvious pattern was `due today`, and **it was measured and rejected**.
Against ten ordinary collector messages, `due today` force-muted **eight** —
"Electricity bill is due today. Pending amount is Rs 1,240." is the commonest
legitimate payment reminder there is, and `pending amount` is already a lure, so
the pair fires. The clock form catches `msg_022` just as well and false-positives
on **one** of the same ten. Same catch, an eighth of the blast radius.

This is the DECISIONS.md rule that bare immediacy words are not deadlines,
applied to a family that looked exempt from it.

### Blast radius

**Exactly one row changed** across all 110: `msg_022`, `notify/payment/0.87` →
`mute/scam/0.82`. Zero of the 30 labelled samples change. `msg_021` still clears
(it carries the coercion signal and no lure — the intended asymmetry). Sample
scores unmoved at 28/30 action, 25/30 message_type. `gate_m2` still PASS with
0 false positives across 23 trusted senders, blindness intact.

### The first attempt was rejected, and that is the point

The initial implementation also added lure patterns, injection patterns, and
widened `_REQUEST_VERB`. Three adversarial reviewers ran against it and two
returned NEEDS-CHANGE:

- **A confirmed regression.** Widening `_REQUEST_VERB` broke the *pre-existing*
  credential negation guard: "Bank staff will never phone you and then open a
  form to collect your CVV" force-muted as scam. That is the `msg_093` FedEx
  class — the fourth appearance of this repo's signature negation bug, caused by
  fixing a different negation bug one family over.
- **Both additions were unnecessary.** Ablation showed `msg_022` mutes without
  the new lures, and `msg_109` mutes without the new injections.

The shipped fix is the intersection: one pattern, one row. **A pre-registered
rollback gate and reviewers told to refute rather than confirm are the only
reasons this reads as a finding instead of a regression we shipped.**

### Known consequence

`payment` is now emitted zero times across the 110 rows. `msg_022` was the last
one and it was wrong there. The labelled sample set contains no `payment` row, so
there is no evidence about how the labeller uses that type; we did not chase it.
