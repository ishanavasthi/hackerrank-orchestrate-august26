# The two-minute pitch

The opener is fixed: *"Can you give me a quick two-minute pitch of what you built and
the problem it solves?"* This is the one answer worth having close to memorised —
everything after it is improvised off whatever you say here.

---

## The pitch (say this)

> **The problem.** WhatsApp is a single stream with wildly different things in it. A
> family group, a society notice board, a school circular, a courier update, a
> promotional blast, a voice note from your mother, and a phishing message that wants
> your OTP — all in the same inbox, all making the same sound. That produces two
> failures at once: the important thing gets missed, and the unwanted or dangerous
> thing interrupts you.
>
> **What I built.** A notification router. For every incoming message it decides one of
> three things — interrupt now, hold for the digest, or suppress — and it does that
> personalised to the receiving user, across text, image posters and screenshots, and
> voice notes. It also emits a message type, a one-sentence human-readable reason, a
> confidence, and citations into that user's own history that justify the call.
>
> **The core design idea.** Risk and preference are two different questions, and I
> decided very early that they must not be answered by the same reader. So the pipeline
> has a safety gate that runs first and is *structurally blind* to how much the user
> likes the sender — it can see message content and hard sender facts like verification
> status, domain, account age and report counts, but it physically cannot see engagement
> history. The spec says clear risk gets muted "regardless of the user's usual
> engagement", and ordering alone doesn't buy you that: a stage that can see "this user
> replies to this sender constantly" can rationalise its way out of a correct flag.
> Withholding the context is what actually enforces the rule.
>
> **What clears the gate** goes to personalisation, which is where taste, history and
> timing apply — group mute state, dismissal rates, whether the user opted out of
> promotions from this sender, quiet hours, and notification load measured against that
> user's *own* baseline rather than a global constant. Then a deterministic pass adds
> evidence retrieval and confidence, and a validator re-checks the file from disk the
> way a grader would.
>
> **The split I ended up with is a hybrid, and I picked it by measuring.** The safety
> gate is deterministic rules; personalisation runs on an LLM. I tried it the other way
> — an LLM safety classifier force-muted twice as many messages and produced six false
> positives on verified, clean-domain senders, muting a bank for "vague urgency framing".
> That breaks the gate's one contract. Personalisation is the opposite: the LLM is
> massively better than my rules there, 93% versus 70% on action.
>
> **Where it landed.** 93% on action and 87% on message type against the thirty labelled
> examples. Every model response is committed to disk, so the whole thing reproduces the
> submitted file byte-for-byte, offline, with no API key set. Python standard library
> only, no install step. And there's a full rules-only fallback path that still produces
> a valid submission with no model at all.

That's about 110 seconds spoken at a normal pace.

---

## The 30-second version (if they cut you off or ask for a recap later)

> It's a three-stage router for WhatsApp notifications. A blind safety gate decides
> risk first and can't see how much you like the sender — that's what makes "mute
> scams regardless of engagement" real rather than aspirational. What survives goes to
> personalisation, which is where mute state, promotion consent, quiet hours and
> notification load apply. Then evidence and confidence are computed deterministically.
> 93% action accuracy, reproducible offline with no key.

---

## The "why it matters" line

If you want one sentence that elevates it above a classification exercise:

> The interesting part of this problem isn't classifying messages — it's that the same
> message is genuinely a *different* decision for two different users, and that a
> system with the power to suppress messages has to be auditable about *why* it
> suppressed one.

---

## Hooks you are deliberately planting

Say these words on purpose. Each has a deep answer behind it in `06-followups.md`:

- **"structurally blind"** → leads to the enforcement mechanism, the whitelist and the tripwire.
- **"I picked it by measuring"** → leads to the bake-off numbers and the six false positives.
- **"reproduces byte-for-byte"** → leads to the cache, and then to the best story you have (the re-run experiment).
- **"personalised to the receiving user"** → leads to concrete two-user examples.
- **"a valid submission with no model at all"** → leads to fallback design and degradation philosophy.

## Hooks to avoid unless you want to go there

- Don't say "agent" or "agentic" — the system is a deterministic pipeline with one LLM
  call per message. Calling it an agent invites a question you'd have to walk back.
- Don't say "fine-tuned", "trained" or "fitted". Nothing here is fitted. Say
  **"inferred from the labelled samples"** and be ready to explain the difference.
- Don't claim calibrated probabilities. The confidence column is not a probability and
  you should say so before you're asked.
