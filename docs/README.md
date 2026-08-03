# Interview prep — Message Notification Router

Everything here is written the way you'd **say** it, not the way you'd document it.
No code walkthroughs. Read `01` and `07` if you only have ten minutes.

| File | What it's for |
|---|---|
| [01-pitch.md](./01-pitch.md) | The fixed opener. Two-minute pitch, plus a 30-second version and the "so what" line. |
| [02-architecture.md](./02-architecture.md) | The system out loud: components, data flow, what talks to what. |
| [03-design-decisions.md](./03-design-decisions.md) | The dozen calls that define the system, each with the *why*. |
| [04-tradeoffs.md](./04-tradeoffs.md) | What I optimised for, what I gave up, what I rejected and on what evidence. |
| [05-limitations.md](./05-limitations.md) | Known weaknesses and the edge cases, stated before they're asked about. |
| [06-followups.md](./06-followups.md) | Branching Q&A — every likely follow-up off every answer above, with talking points. |
| [07-cheatsheet.md](./07-cheatsheet.md) | Numbers, names and one-liners to have on the tip of your tongue. |

`index.html` in this folder is all of it on one browsable page. Open it in a browser.

---

## How to use this in the room

The interviewer builds each question off your last answer. That means **the words you
choose are the next question**. Three things follow:

1. **Seed deliberately.** Every answer should leave one or two hooks you *want* pulled —
   "blind safety gate", "the cache is the artifact", "the model isn't reproducible",
   "a promotion never interrupts". All four have deep, confident answers behind them
   in `06`. Don't seed something you can't go three levels deep on.
2. **Lead with the decision, follow with the evidence.** "I split risk and preference
   into two stages" → then the reason → then the measurement. Not the other way around.
3. **Volunteer the limitation before it's found.** This project's strongest material is
   the stuff that went wrong and got measured: the re-run experiment, the brand-mismatch
   lead that was true-but-wrong, the guard that had silently drifted. Confidence here
   reads as rigour, not hedging.

## The one theme to keep coming back to

**Risk and preference are different questions, and they must not be answered by the
same reader.** Nearly every design decision in this system is downstream of that
sentence. If you get lost, return to it.
