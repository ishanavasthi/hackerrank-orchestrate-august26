# System architecture — how to describe it out loud

## The one-breath version

> Four things in a line: assemble the context, gate for risk, personalise what
> survives, then write and re-validate. Two of those four stages are deterministic on
> purpose, and one model call sits in the middle.

## The whiteboard picture

```
  12 CSVs ─┐
           ├──► CONTEXT ASSEMBLY ──► ① SAFETY GATE ──► ② PERSONALISATION ──► ③ POST-PROCESS ──► ④ WRITE + VALIDATE
 media.json┘     (one object per       (blind, rules)     (signals + LLM)      affinity →           output.csv
                  message)                   │                                 promotion rule →      + grader-style
                                             │                                 evidence →            re-check
                                             └── force-muted rows ─────────────►confidence
                                                 (skip stage ②, still get
                                                  evidence + confidence)
```

Two things to say about that picture, because they're the parts people ask about:

- **Force-muted rows leave the main path but not the pipeline.** They skip
  personalisation entirely — that's the whole point, a trusted-sender signal never gets
  a chance to argue a scam back down — but they still get evidence retrieval and
  confidence calibration. That was a real fix: they used to ship with no evidence at
  all, and a history row the user *reported* is excellent justification for suppressing
  a similar one.
- **The model sits in exactly one place.** One call per message, in stage ②. Everything
  before it and everything after it is deterministic.

---

## Stage by stage

### Context assembly

Twelve CSVs get loaded once and indexed, then each message is turned into a single
context object holding: the message, the extracted media text, the recipient's user
row, the group, the recipient's membership in that group, **the sender's membership in
that group**, the business account, this user's relationship with that business, the
user's message history, the recorded outcome for each of those history rows, and the
user's daily notification volumes.

The one worth calling out is **the sender's membership**. I was joining the group
members table only on the recipient — "what is the reader's standing?" — and never on
the sender. So an admin notice and a message from someone who joined last week rendered
identically to every downstream stage. Adding the second join is what settled the
hardest judgement call in the project (see the msg_021/msg_022 pair in `05`).

### Stage ① — the blind safety gate

Runs first, and can force `mute` with type `scam` on its own authority. It fires on
**risk**, meaning deception: credential extraction, impersonation, coercion toward a
payment or verification action, and messages that address the router rather than the
recipient. It deliberately does *not* fire on merely annoying or low-value promotional
content — a gate that mutes boring marketing is a gate that will eventually mute
something the user wanted.

What it can see: message text, extracted media text, forward count, and structural
sender facts — verified flag, official domain versus the domain actually used, account
age, the age of the domain being used, and the global count of reports against that
sender. Plus the sender's role and tenure in a group.

What it cannot see: anything about how *this* user has behaved toward this sender.

The decision rule, in words: **a credential request on its own is disqualifying, and so
is an attempt to instruct the router. Coercion alone or a lure alone is ordinary
marketing urgency — together, they're the standard phishing shape.** Structurally, an
impersonating domain is enough on its own, but a domain mismatch alone is *not*
impersonation; it needs a corroborating signal.

It force-mutes 22 of the 110 messages.

### Stage ② — personalisation

The 88 messages that clear the gate get a set of signals computed for them —
about a dozen booleans plus two rates. Direct mention of this recipient, group mute
state, group dismissal rate, chain-letter shape, genuine urgency, the sender's own
defusing language ("nothing urgent"), promotional content, promotion consent, whether
the business relationship is stale or active, quiet hours, notification load against
that user's own median, unknown sender, heavy forwarding, and whether the media
transcript came back incomplete.

Then the LLM makes the call, with those signals rendered into its prompt as
precomputed facts. The rules engine can also make the call on its own — that's the
offline fallback.

**The important architectural property here:** the provider flag chooses *how* a
decision is made, never *whether* personalisation runs. That wasn't always true. There
was a branch where selecting an LLM provider silently skipped the whole personalisation
stage — group mute state, promotion consent, quiet hours, the spec's own carve-out,
none of it ran, and nothing errored. It was hidden by a second bug, where the `.env`
file loaded *after* argparse resolved its default, so the configured provider was being
ignored entirely. Fixing either bug alone would have shipped a worse system than
leaving both.

### Stage ③ — post-processing (deterministic, both paths)

Four steps, and **the order is load-bearing, not stylistic**:

1. **Affinity override.** Does the user have an *open obligation* with this business —
   a booking, a delivery, an order, a prescription? If so, upgrade a `digest` to
   `notify`. It can only ever raise attention, never lower it, and that's enforced at
   import time rather than by convention.
2. **The promotion invariant.** No row this system emits may pair `notify` with
   `promotion`. A product rule, not something learned from the data.
3. **Evidence retrieval.** A scored search over the user's own history.
4. **Confidence calibration.**

Steps 3 and 4 are both keyed on the final action, which is why 1 and 2 must come first.
Apply the promotion demotion after calibration and you ship a `digest` row carrying a
`notify` confidence.

All four run identically on the rules path and the LLM path, so the two engines can
never disagree about a user's relationship or cite different evidence for the same row.

### Stage ④ — write and validate

The writer emits the exact six columns in the exact order. Then `main.py` shells out to
the validator **as a subprocess**, so it reads the finished file off disk exactly as a
grader would, rather than checking the in-memory objects that produced it. The
validator imports nothing from the pipeline except the shared enum definitions.

---

## The media pipeline (say this as a separate sub-system)

33 media files: 20 images, 13 voice notes. They're extracted **once, offline, ahead of
everything else**, into a committed JSON cache. The router itself never calls a vision
or speech provider.

- Images: Gemini, with reasoning explicitly disabled — OCR is transcription, not
  reasoning, and with thinking on, the dense images spent their entire token budget
  deliberating and returned about 77 tokens of actual output.
- Voice: Groq's Whisper turbo. 13 files in 5.3 seconds, all clean.
- Every extraction is **structurally validated before it's cached** — all four required
  fields present, a non-truncated finish reason, no filler-character degeneration.
  That's not defensive coding for its own sake: I caught two separate providers
  returning HTTP 200 with unusable content, and a cached bad extraction is invisible
  downstream and permanent.

Sequencing that pass first was itself a decision. The original plan had media last, on
"order by risk". Once I sized it — 110 messages, 33 files, 11 MB, no cost or latency
pressure anywhere — the real open risk wasn't cost, it was *what was inside the media*.
Dense text posters versus payment screenshots versus code-mixed audio changes the prompt
for every downstream stage. Deferring it would have meant designing stages one and two
against a guess about their own input.

---

## Verification (this is a component, not an afterthought)

Four checks behind one exit code:

- **Contract validation** — column order, one row per message id, enum membership,
  confidence range, single-line reasons, and that every cited evidence id actually
  resolves in the history file.
- **The safety-gate assertions** — the must-mute set holds, no trusted sender is ever
  falsely muted, and blindness is proven by scanning all 110 rendered prompts for 21
  forbidden engagement field names.
- **Edge-case assertions** — the four named edge classes, plus a guard that no shipped
  row was decided by an error handler.
- **A smoke test** against the 30 labelled samples — deliberately a floor, not a target.
  It warns on a collapse and otherwise just prints, so it can never become something I
  tune toward.

**Two of those checks self-test their own guards before checking the artifact**, and
that's worth mentioning because of how it came about. The silent-fallback guard was a
hand-maintained list of marker strings that had drifted from what the code actually
emits — it caught one of four degradation paths and reported PASS. A guard that has
drifted from the thing it guards is worse than no guard, because it's green.
