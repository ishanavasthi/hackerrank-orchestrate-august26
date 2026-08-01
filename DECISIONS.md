# DECISIONS.md

Running log of nontrivial technical and architectural calls for the Message
Notification Router. Written to be explainable out loud, not to look good.

Entries below the divider were backfilled at the end of the design phase,
before any code was written.

---

## Safety gate runs before personalization, and runs blind
Decision: A risk stage runs first and can force `mute` on its own. It sees message content plus structural sender facts (`verified`, `official_domain` vs `domain_used_by_sender`, `account_age_days`, `user_reports_30d`, `forwarded_count`) but is deliberately not given the user's engagement history.
Alternatives considered: One LLM call weighing safety and personalization together; or a safety stage that runs first but still sees the full user context.
Why: The spec says scam/risk is muted "regardless of the user's usual engagement," so trust signals must not be able to argue it down. Ordering alone doesn't achieve that — if the stage can see that the user replies to this sender constantly, it can still rationalize. Withholding the context is what actually enforces the rule. `msg_091` is the proof case: an OTP-phishing message from a *personal* contact who may have strong engagement history.
Trade-off / what we gave up: Costs an extra LLM call per message, and we lose real signal — a message that looks risky in isolation but is obviously fine given a 3-year relationship may get muted. We accepted a false-positive bias on safety because the spec explicitly asks for it.

## Determinism via temperature 0 plus on-disk caching
Decision: All LLM calls at temperature 0; results cached to disk keyed by `message_id` (routing) and `media_id` (extraction). A rerun reads cache and reproduces `output.csv` byte-for-byte.
Alternatives considered: Temperature 0 alone with no cache; or self-consistency sampling (n>1, majority vote) for better accuracy.
Why: The contract says "deterministic where possible." Temperature 0 alone is not actually sufficient — the same prompt can still drift across runs. The cache is what makes the guarantee real. Sampling was rejected because it directly contradicts determinism.
Trade-off / what we gave up: Self-consistency probably buys a bit of accuracy on borderline calls, and we're giving that up. Cache invalidation becomes our problem — if a prompt changes, stale entries must be cleared or we'll silently ship old decisions.

## Evidence = resembles the message AND has an outcome that explains the decision
Decision: `evidence_message_ids` come from `message_history.csv` rows scoped to the same user, ranked by similarity to the current message *and* by whether the joined `message_events.csv` outcome supports the action we chose. Emit 1–2 IDs, `none` when nothing fits.
Alternatives considered: Most-recent-message-from-this-sender; pure similarity ranking with no outcome check; emitting a longer ranked list to raise hit odds.
Why: The grader checks whether evidence points to *relevant* history. A recent message that the user ignored explains nothing about a `notify`. `message_events` joins 1:1 with all 412 history rows, so an outcome is always available — there's no reason not to use it.
Trade-off / what we gave up: The 1–2 ID cap is the weak part. We inferred it from 30 sample rows (27 used one ID, 3 used two), which is thin evidence about what the grader actually rewards. A longer list might score better on recall; we chose to match observed format instead of gambling.

## Few-shot from sample_messages.csv for style only
Decision: Use a handful of `sample_messages.csv` rows as few-shot examples to calibrate `reason` phrasing and the confidence scale. Do not use their label distribution as a prior on the test set.
Alternatives considered: Not using the samples at all, to stay clearly inside the no-hardcoded-labels rule; or fitting our thresholds to reproduce all 30 sample labels.
Why: `problem_statement.md` line 30 explicitly says to use them "to understand the expected output format and style," so this is sanctioned rather than a grey area. Leakage is structurally impossible — sample IDs are `sample_msg_*`, test IDs are `msg_*`, zero overlap, so no sample label can land on a test row. The 9/11/10 action split is too uniform to be a real class balance; it reads as a curated teaching set.
Trade-off / what we gave up: We're inferring the target confidence band (0.78–0.91) from 30 rows, which is a small sample and could just be an artifact of how the examples were written. If the real ground truth uses wider confidence, our calibration is systematically too timid.

## Dropped the cheap-first-pass media filter
Decision: Process all 33 media files (20 JPG, 13 MP3) with the full model in one up-front pass. No tiered cheap-filter-then-deep-analysis design.
Alternatives considered: A cheap classifier deciding which media deserves expensive analysis — the plan we'd have needed if volume were high.
Why: Sizing killed the premise. 110 messages, 33 distinct media files, 11 MB total. There is no cost or latency pressure anywhere in this problem, so the filter would add a real failure mode (cheap tier misroutes something away from deep analysis) to buy nothing measurable.
Trade-off / what we gave up: Nothing meaningful at this dataset size. Worth noting the design wouldn't survive a scale-up — at 100k messages the tiered approach becomes correct again.

## Media extraction moved ahead of the end-to-end skeleton [PENDING CONFIRMATION]
Decision (proposed, not yet approved): Run media OCR/ASR as M0, before the thin end-to-end skeleton, rather than at milestone 3 after the text depth pass.
Alternatives considered: The original ordering — skeleton first, text depth second, media third — on the principle of ordering by risk rather than by ease.
Why: Same principle, different answer once the numbers landed. The cost risk that justified deferring media is zero. The actual open risk is what's *inside* the media — dense text posters vs payment screenshots vs photos, English vs code-mixed audio — and that answer shapes the prompt for every downstream stage. Deferring it means designing stages 1–2 against a guess about their own input.
Trade-off / what we gave up: Front-loads ~45 minutes before anything is end-to-end runnable, which delays the moment we have a valid `output.csv` to fall back on. If we run out of time, having the skeleton earlier would have been the safer hedge.

## Mixed providers: Groq for ASR, Gemini for OCR, Claude Haiku or NVIDIA NIM for routing
Decision: Three providers, one per modality. Groq `whisper-large-v3-turbo` transcribes the 13 voice notes, Gemini does OCR and description on the 20 images (model since corrected — see the bake-off entry), and the routing decisions run on either Claude Haiku 4.5 (`claude-haiku-4-5`, $1/$5 per MTok) or NVIDIA NIM (free tier, OpenAI-compatible) — picked by benchmark, not upfront. Keys live in `.env` only, listed in `.env.example`, and `.env` is gitignored.
Alternatives considered: One vendor for everything — a single Claude key doing vision, ASR, and routing, which is what I originally proposed.
Why: Quota and fit line up per modality rather than per vendor. Groq gives dedicated free Whisper capacity (2,000 req/day) that no general-purpose LLM quota touches, and Gemini's free tier covers 20 images at zero cost. Neither competes with the routing budget, so a bad routing run can't exhaust the media budget or vice versa. Splitting also means the two candidate routers can be swapped behind one interface without touching the media stage at all.
Trade-off / what we gave up: Three SDKs, three failure modes, three sets of rate-limit semantics, and three accounts a grader would need to reproduce from scratch — against one vendor where all of that is uniform. We also lose cross-stage prompt caching. Honest caveat: at 33 media files and 110 messages, single-vendor cost would have been roughly a dollar, so this is a quota-independence and fit decision, not a cost saving.

## Media cache is the determinism boundary, not an optimization
Decision: `code/cache/media.json` (keyed by `media_id`) is committed to the repo, and the router reads only from it — it never calls Groq or Gemini at routing time.
Alternatives considered: Treating the cache as a local speed-up and re-extracting media on each run; or gitignoring it as a build artifact.
Why: The determinism promise in an earlier entry assumed temperature 0 on one vendor. Groq and Gemini free tiers make no reproducibility guarantee we can rely on, so temperature 0 alone no longer buys determinism across providers. Freezing extraction output into a committed file is what actually makes two runs produce identical `output.csv`. It also means the submission runs end-to-end without Groq or Gemini keys at all.
Trade-off / what we gave up: A stale cache silently ships old transcriptions if a prompt changes and we forget to clear it — the same invalidation risk flagged in the determinism entry, now with a second way to bite. Committing derived model output also makes the repo less obviously "run it from scratch"; a reviewer has to be told the cache is regenerable.

## Deferred: media model choice and DND handling
Decision: Not yet made. Two questions are open and were deliberately not guessed at.
Alternatives considered: (a) Which model handles the 33 media files. (b) Whether a `do_not_disturb_window` should downgrade `notify` to `digest`, or whether DND is a delivery-layer concern outside routing.
Why: The DND call is a genuine toss-up and materially shifts the notify/digest split — every user has a DND window and real messages land inside them (`msg_023` at 22:19). Both readings are defensible from the spec, and there's no evidence in the data that settles it, so guessing would bake in an unexamined assumption across all 110 rows.
Trade-off / what we gave up: Blocking on these costs a little time up front. Judged cheaper than discovering the wrong assumption at M4 and re-running everything.

## Vision provider bake-off: kept Gemini, rejected NIM for OCR
Decision: Keep Gemini for image OCR. `VISION_PROVIDER` (gemini|nim|both) stays in the code so the choice is re-testable, but the default is Gemini and the committed cache was produced by it.
Alternatives considered: Consolidating OCR onto NVIDIA NIM to drop one provider — `nemotron-nano-12b-v2-vl` (NVIDIA's document/OCR-oriented VL model) and `llama-3.2-90b-vision-instruct` as the runner-up.
Why: Measured on the 5 hardest images, not argued. Gemini went 5/5 clean in 35s. Nemotron went 3/5 — it degenerated into a repeated-underscore loop on the scanned consent form, burned its whole token budget, and returned HTTP 200 with unusable text, then hard-500'd on a rerun of the same two images. Llama-90b survived those two but returned `KEY_DETAILS: NONE` on images that plainly contained an organisation name and a price, and that field is what feeds payment/urgency detection in the safety gate. The premise that motivated the swap — determinism — was also wrong: NIM is a hosted endpoint with no reproducibility guarantee either.
Trade-off / what we gave up: We keep three providers and the extra account, SDK, and rate-limit surface that costs. We also stay exposed to Gemini's free-tier quota, which is the real constraint (below).

## Extraction must be structurally validated, not trusted on HTTP 200
Decision: Every extraction is checked for all four required labels, a non-truncated finish reason, and filler-character dominance. Anything failing is marked `problems` and excluded from the cache.
Alternatives considered: Trusting HTTP status and caching whatever came back, which is what the first version of the extractor did.
Why: Two distinct silent failures showed up within an hour of each other. Nemotron returned 200 with a degenerate underscore loop. Gemini 3.x returned 200 with a truncated fragment of its own raw reasoning, because on that family the token cap covers thinking plus output and a dense image spends the entire budget thinking. Both would have been cached as good data and silently corrupted every routing decision for those images. The validator caught both.
Trade-off / what we gave up: A little extra code and the risk of false rejections on a legitimately terse extraction. Worth it — a wrong cache entry is invisible downstream, and the cache is committed, so a bad entry persists until someone re-reads the JSON by hand.

## Thinking disabled for OCR calls
Decision: Gemini extraction calls set `thinkingConfig.thinkingBudget = 0` and `maxOutputTokens = 4096`.
Alternatives considered: Leaving thinking on and simply raising the token cap.
Why: OCR is transcription, not reasoning. With thinking on, the bank-statement and prescription images spent ~3,200 tokens deliberating and emitted ~77 tokens of output before hitting the cap. Disabling thinking fixed both immediately and made extraction faster and cheaper. Raising the cap alone would have paid for reasoning we do not want.
Trade-off / what we gave up: Possibly a little accuracy on genuinely ambiguous images where reasoning would help disambiguate layout. Nothing in the 20-image set looked like that, but we did not test it directly.

## Gemini free tier is 20 requests/day — the cache is what makes this viable
Decision: Accept the quota and lean on the committed cache; document the per-model pool workaround (`GEMINI_VISION_MODEL`).
Alternatives considered: Moving to NIM purely for quota headroom (40 req/min), or paying for Gemini.
Why: Measured, not assumed — `gemini-3.6-flash` returned `limit: 20` for `generate_content_free_tier_requests` after the bake-off consumed the day's allowance. Our image count is exactly 20, so there is no headroom for a retry or a prompt revision. Quota pools turned out to be per-model, so the remaining 6 images were finished on `gemini-3.5-flash`. This is tolerable only because extraction happens once and is committed; the router never calls Gemini.
Trade-off / what we gave up: A prompt change to the extractor now costs a full day of quota, or a model switch. The final cache is also split across two models (14 on 3.6-flash, 6 on 3.5-flash), recorded per entry — not ideal for consistency, and worth a uniform re-run when quota resets if time allows.

## Empirically confirmed: temperature 0 does not give reproducibility
Decision: No change to the design, but the earlier determinism entry is now backed by a measurement rather than an assumption.
Alternatives considered: Continuing to assume temperature 0 was sufficient and treating the cache as an optimisation.
Why: Ran the same image through Gemini twice at temperature 0 and hashed both outputs — they differed. This is the direct evidence for the claim made in "Media cache is the determinism boundary". If challenged on it in an interview, the answer is a measurement, not a belief.
Trade-off / what we gave up: Nothing. It confirms a decision we had already made for other reasons.

## ASR bake-off: Groq Whisper for voice notes, NIM omni rejected on reliability
Decision: Transcribe the 13 voice notes with Groq `whisper-large-v3-turbo`. `ASR_PROVIDER` (groq|nim|both) stays in the code so the choice is re-testable.
Alternatives considered: NVIDIA NIM `nemotron-3-nano-omni-30b-a3b-reasoning`, the only speech-capable model in the NIM catalogue — attractive because it would have collapsed a provider.
Why: On 5 files the two agreed exactly on 2 (including a 41s clip), and NIM was actually *better* on one — it produced "call when free" where Whisper produced the nonsensical "call went free". But NIM timed out at 180s on a 7.9s file, a 1-in-5 failure rate, and ran 4x to 30x slower where it did succeed. Groq did all 13 files in 5.3 seconds wall clock with 13/13 clean. For a one-time extraction feeding a committed cache, reliability beats a marginal wording edge.
Trade-off / what we gave up: Whisper makes occasional small transcription errors ("call went free"), and NIM would have caught at least one of them. We keep a third provider we could otherwise have dropped. Neither observed error changed the routing-relevant meaning of its message.

## Nearly rejected NIM for the wrong reason — the payload shape was mine, not the model's
Decision: Record this as a process note, not just an outcome. The NIM audio block is `audio_url` with a data URI, not the OpenAI-style `input_audio`.
Alternatives considered: Concluding from the first run that the omni model simply cannot do ASR.
Why: The first bake-off had NIM returning "I'm unable to transcribe the voice note without the audio file" on every file. That reads as a model incapability, and I was one step from writing it up that way. The endpoint had accepted `input_audio` with HTTP 200 and silently dropped the audio. Switching to `audio_url` produced a correct transcript immediately. The lesson generalises: when a provider appears incapable, rule out your own request shape before recording a verdict about the model.
Trade-off / what we gave up: A round trip of extra probing. Cheap against publishing a wrong conclusion in a document meant to be defended out loud.

## Validator missed a silent failure because of a curly apostrophe
Decision: Refusal detection normalises smart punctuation and scans the first 200 characters, rather than matching a straight apostrophe at index 0.
Alternatives considered: Leaving the original startswith check, which looked reasonable when written.
Why: nemotron-omni refused with "I’m unable to transcribe..." using U+2019. The check tested `startswith("i'm unable")` with a straight apostrophe and passed the refusal through as a clean transcript. Two files were marked ok with no transcript content at all. This is the third silent HTTP-200 failure across M0, and the first one my own validator failed to catch — which is the more useful data point: a validator is only as good as the failure modes you have actually seen.
Trade-off / what we gave up: Broader substring matching risks rejecting a genuine transcript that happens to contain "please provide" in the first 200 characters. Judged acceptable — a false rejection is visible and re-runnable, a false accept is invisible and permanent in a committed cache.

## Known caveat: three transcripts begin mid-sentence
Decision: Ship them as-is and record the caveat rather than silently accepting or hand-fixing them.
Alternatives considered: Flagging them as failures in the validator, or manually re-cutting the audio.
Why: vn_007, vn_013 and vn_014 begin mid-sentence and run at 6-11 characters per second against a 12-21 norm. That is consistent with two different causes — robocalls containing pauses and hold music, or source recordings that genuinely start mid-call — and distinguishing them needs someone to listen to the audio. All three are unambiguously promotional or telemarketing content, so the routing decision is the same under either explanation. The validator's characters-per-second floor deliberately did not fire, because these look legitimate.
Trade-off / what we gave up: If ASR did drop leading audio, we lose whatever was in it. Accepted because the routing call does not turn on it; worth revisiting only if one of these three is misrouted during evaluation.
