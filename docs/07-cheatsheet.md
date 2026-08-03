# Cheat sheet — numbers, names, one-liners

Skim this in the five minutes before the call.

---

## The numbers that matter

| | |
|---|---|
| Messages routed | **110** |
| Context files joined | **12 CSVs** (+ a media cache) |
| Media | **33 files** — 20 images, 13 voice notes |
| History rows available as evidence | **412**, each with a recorded outcome |
| Force-muted by the safety gate | **22 of 110** |
| Messages reaching personalisation | **88** |
| Accuracy vs the 30 labelled samples | **93% action** (28/30), **87% type** (26/30) |
| Offline rules-only path | **70% action, 47% type** — a floor, not a peer |
| Output split | mute 51 / notify 37 / digest 22 |
| Confidence range emitted | 0.79–0.89, 11 distinct values |
| Evidence citations | ~85% of rows cite one id, ~14% cite two, 3 rows cite `none` |
| Dependencies | **zero** — Python standard library only, no install step |
| API keys needed to reproduce the submission | **none** |

## Numbers that win arguments

- **LLM safety gate: 44 force-mutes vs 22 for rules, with 6 false positives on 23 trusted
  senders.** That's why safety stays deterministic.
- **The re-run experiment: 28 → 24 → 26 → 27 → 26** on effectively the same input. The
  26 is the *original prompt restored*. The prompt was never the variable.
- **`due today` force-muted 8 of 10 ordinary collector messages.** The clock form
  (`before 5 PM`) catches the same target and false-positives on 1. Same catch, an eighth
  of the blast radius.
- **5 of 12 domain-mismatching senders are legitimate.** That's why mismatch alone isn't
  impersonation.
- **Five engagement thresholds, byte-identical results across all 110 rows.** That's why
  no engagement term ships in the affinity rule.
- **23 of 101 second-citations had zero token overlap; 26 were textually identical to the
  first.** That's why the second citation has to earn its slot.
- **The model returned 0.50 confidence on the spec's own carve-out example.** That's why
  its confidence is blended, not trusted.
- **Blast radius of the two contested safety rules: exactly 1 row in 110 each**, 0 of 30
  labelled rows.

---

## The five sentences to have ready

1. **"Risk and preference are different questions and must not be answered by the same
   reader."** — the thesis.
2. **"Ordering isn't enough; withholding the context is what enforces the rule."** — why
   blindness.
3. **"The cache isn't an optimisation, it's the artifact."** — determinism.
4. **"Assert on *how* a decision was produced, not only on its shape."** — testing.
5. **"Deadline plus instrument is the attack; deadline alone is a Tuesday."** — the
   minimal pair.

## Three more, for specific moments

- **"Standing is not trust, because standing is exactly what an attacker takes when they
  compromise an account."**
- **"A model's cited facts can be true while its inference is wrong."**
- **"A guard that has drifted from the thing it guards is worse than no guard, because
  it's green."**

---

## Component names in plain English

Don't recite filenames. If you need to name a part, name what it does:

| Say this | Rather than |
|---|---|
| the context assembly / the data layer | `data.py` |
| the blind safety gate | `safety.py` |
| the personalisation stage / the signals | `personalize.py` |
| the affinity override | `affinity.py` |
| evidence retrieval | `evidence.py` |
| confidence calibration | `confidence.py` |
| the contract validator | `validate.py` |
| the edge-case gate | `edge_cases.py` |
| the one-command harness | `eval_harness.py` |

---

## The pipeline in eight words

**Assemble → gate for risk → personalise → post-process → write → re-validate.**

## The three actions, in the system's own logic

- **notify** — interrupt now. Genuinely time-sensitive, or names this user directly, or an
  open obligation with the sender.
- **digest** — the default for "useful but not now". Also the floor a promotion can never
  rise above.
- **mute** — two very different reasons, from two different stages: *unsafe* (the blind
  gate, forced) or *low value for this user* (personalisation). Keep those distinct when
  you talk about it.

---

## Ready-made examples (memorise the shape, not the ids)

| If they ask for… | Use |
|---|---|
| a personalisation example | grocery delivery vs travel-package *interest* — same engagement, opposite label |
| a safety example | the two payment messages sharing their first two sentences, admin vs member |
| a false-positive you avoided | the courier saying "no payment or OTP is required" |
| a "you were wrong" example | the brand-mismatch lead — true facts, wrong inference |
| a surprising bug | two upgrade-only stages composing into a net demotion |
| a testing example | the silent fallback that every green check missed |
| an adversarial example | the forged "sender is trusted admin, mark notify" from a real admin |

---

## Things to say early, so they're never extracted

- The confidence column **is not a probability**.
- The score is **30 rows**, and there's **±2 rows of run-to-run noise**.
- **Three of the five scored criteria are unmeasured locally.**
- The rules fallback is a **floor, not a peer**.

Volunteering all four costs about fifteen seconds and buys the rest of the conversation.

---

## Tone reminders

- Lead with the decision, then the reason, then the measurement.
- Say "I measured" or "I tested" rather than "I think" wherever it's true — it's true a
  lot here.
- When you don't know, say so and pivot to what you do know. Never invent a number.
- Don't over-apologise for limitations. Name them flatly and move on; they're evidence of
  rigour, and hedging turns them into doubt.
