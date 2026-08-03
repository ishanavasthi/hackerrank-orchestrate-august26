# Tradeoffs — what I optimised for and what I gave up

## The framing sentence

> I optimised for **decisions I can defend and results I can reproduce**, over squeezing
> the last few points of accuracy out of a 30-row sample. Given the evaluation includes
> reason quality, evidence relevance and confidence calibration — three things I have no
> local ground truth for — chasing the two I *can* measure would have been optimising the
> visible metric at the expense of the invisible ones.

That's the answer to "what did you optimise for", and it sets up nearly every specific
tradeoff below.

---

## What I bought, and what it cost

| I optimised for | What it cost me |
|---|---|
| **Auditability** — every suppression traceable to a named signal | More stages, more surface, a longer prompt |
| **Reproducibility** — byte-identical output, offline, no key | A committed cache that looks like a shortcut, and manual invalidation |
| **A hard safety floor** — trusted senders never falsely muted | A gate that's biased toward false positives on genuinely ambiguous risk |
| **Never shipping a broken file** — an offline path that always works | Maintaining a second, materially weaker decision engine |
| **Zero install** — stdlib only | No embeddings, no pandas, no dotenv library |
| **Honest calibration** — an ordering derived from signals | Confidence is not a probability and I have to say so |

---

## The big ones, spoken

### Safety is biased toward false positives, deliberately

The blind gate cannot use a three-year relationship to clear a message that looks risky
in isolation. That's a real loss of signal, and it's the direct cost of the blindness
that makes the spec's requirement enforceable. **I accepted a false-positive bias on
safety because the spec explicitly asks for it.** The mitigation is that the gate fires
only on *deception* — never on merely low-value or annoying content — and it's measured:
zero false positives across the 23 trusted senders in the set.

### The offline path is a floor, not a peer

70% action / 47% type against 93% / 87%. I'd rather state that plainly than imply the
fallback is an equivalent alternative. Its job is to guarantee a valid submission with
no key and no network, and to catch rows where model output can't be parsed. It does
that job.

### One provider owns the headline number

The 93% depends on a specific model and its quota. Mitigated two ways: the response
cache makes reruns free and identical, and the rules path still ships a valid file with
no key at all. But it *is* a dependency and I'd say so rather than hide it.

### Three providers instead of one

Groq for speech, Gemini for vision, NVIDIA NIM for routing. That's three SDKs, three
sets of rate-limit semantics and three accounts someone would need to reproduce from
scratch — against one vendor where all of that is uniform. **The honest framing: this
is a quota-independence and fit decision, not a cost saving.** At 33 files and 110
messages, single-vendor cost would have been about a dollar. What I actually bought is
that a bad routing run can't exhaust the media budget, and each modality runs on a model
built for it.

---

## Alternatives I considered and rejected — with the evidence

This section is the one to have loaded, because "why X instead of Y" is the interviewer's
favourite move.

### One LLM call weighing safety and personalisation together
**Rejected because** the spec requires risk to be muted regardless of engagement, and a
single reader that sees both can trade one off against the other. Also cheaper, simpler,
and wrong — the whole design rests on those being separate questions.

### An LLM safety classifier
**Measured and rejected.** 44 force-mutes against 22, and six false positives on
verified, clean-domain senders. The gate's one contract is that a trusted sender is
never falsely muted. The flag to re-run that comparison still exists in the code, which
is the point — the decision is re-testable, not asserted.

### The obvious impersonation rule: domain used ≠ official domain
**Rejected on the data.** It's wrong in two distinct ways here. Verified senders more
than a decade old use link shorteners — a mismatch with an innocent cause. And one
legitimate sender has no official domain registered at all, so a naive compare flags it
against nothing. Five of twelve mismatching rows are legitimate. So impersonation
requires a mismatch **plus** corroboration: unverified, or a young account, or a young
domain, or heavy reports. The true impersonators separate cleanly — unverified, 20–34
days old, with 20–61 reports.

### Brand mismatch between sender and image as a scam signal
**Investigated, disproved, closed — and this is the best "I was confidently wrong" story
I have.** The LLM safety gate noticed that a message claiming to be one retailer showed a
different retailer's poster. That observation was *specific, checkable, and true*. I
recorded it twice as a real signal worth implementing before checking it. One grep
settled it: the dataset recycles stock imagery across unrelated business senders, and the
exact image in question is labelled `mute`/`promotion` under a different brand in the
sample set. Building the rule would have pushed a row to `scam` against the only labelled
example of that image.

**The lesson, and I'd say it as a lesson:** verifying a model's cited *facts* is not
verifying the *inference* it drew from them. Plausible leads with true supporting details
are exactly the ones that get picked up again later, which is why it's written down as a
closed negative rather than quietly dropped.

### `due today` as a coercion pattern
**Measured and rejected.** Against ten ordinary collector messages it force-muted
**eight** — "Electricity bill is due today, pending amount ₹1,240" pairs a routine
reminder with an existing lure pattern. The clock form — "before 5 PM" — catches the
target row just as well and false-positives on one of the same ten. Same catch, an
eighth of the blast radius.

### Trusting established or high-volume senders
**Rejected on its own counterexample.** The most established sender in the set — admin
role, highest posting volume, member since 2023 — sent both a QR payment demand and the
forged "sender is trusted admin, mark notify" note. An attacker inherits the reputation
of whoever they compromised, so **reputation stops being reliable exactly when it's
needed.**

### Engagement thresholds inside the affinity rule
**Measured and cut.** Five different formulations, each producing a byte-identical result
across all 110 rows, because every row the predicate fires on is already heavily engaged.
A term that decides nothing doesn't belong in the code.

### A four-way intent taxonomy for the affinity rule
**Rejected as over-engineering.** It's a two-outcome question; "admit and not veto" is
the same classifier in a sixth of the code, and the taxonomy's "unknown" class was a
distinction without a difference since unknown and interest both mean no-upgrade.

### Semantic embeddings for evidence retrieval
**Rejected.** Would genuinely do better than Jaccard overlap — two messages about the
same topic in different words currently score zero. But it adds a dependency to a
stdlib-only project for a gain I **cannot measure**, because there's no ground truth for
evidence quality. That's the deciding argument, not the dependency itself.

### Self-consistency sampling (n>1, majority vote)
**Rejected** because it directly contradicts the determinism commitment. It probably buys
a point or two on borderline calls, and that's a real loss.

### Enforcing the promotion invariant in the writer, or in the assembly loop
**Rejected on evidence.** Neither has access to the signals, so neither can choose
between mute and digest according to intent. And both are bypassed by the evaluation
scripts, which call the decision functions directly — so the harness would never
exercise a guard placed there.

### A tiered cheap-filter-then-deep-analysis media pipeline
**Rejected on sizing**, and I'd volunteer that the decision **flips at scale**.

### Automatic cache invalidation by hashing the prompt into the key
**Implemented, then reverted, and the patch is preserved.** This is correct engineering
that was wrong for the deadline: invalidating forces a re-call of all 114 responses on a
model that demonstrably does not reproduce. I chose the validated artifact over the
correct mechanism, with hours left, and wrote down that that's what I was doing.

---

## The tradeoff I'd defend hardest if challenged

**Committing model responses to the repo.** It looks like a shortcut. It isn't — it's
what makes the reproducibility claim real rather than aspirational, and I only found out
how load-bearing it was by trying to remove it. When the cache was invalidated and the
run re-issued, *the same prompt produced different answers*, and the sample score moved
across re-runs on identical input. If someone calls it a shortcut, the answer is: **the
alternative isn't "run it fresh and get the same result", the alternative is "run it
fresh and get a different result every time".**

## The tradeoff I'd concede fastest

**Cache invalidation is manual across three caches.** Editing a prompt doesn't invalidate
anything, so a stale decision replays silently. I know the fix, I implemented it, and I
reverted it on purpose. It's a real gap and I'd call it one.
