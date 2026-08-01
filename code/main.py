#!/usr/bin/env python3
"""Message Notification Router — entry point.

    python code/main.py                      # stub router, no keys needed
    python code/main.py --provider anthropic # live routing
    python code/main.py --validate-only      # re-check an existing output.csv

Reads dataset/ and writes output.csv at the repo root. Secrets come from the
environment (or a local .env) only — never from arguments or source.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = REPO_ROOT / "code"
sys.path.insert(0, str(CODE_DIR))

from contracts import DEFAULT_PROVIDER  # noqa: E402


def load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables always win, so an
    exported key is never silently overridden by a stale file."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
            value = value[1:-1]                      # quoted: take verbatim
        else:
            # Unquoted values may carry a trailing comment. .env.example ships
            # `ROUTER_PROVIDER=nvidia   # anthropic | nvidia`, which without
            # this produced the literal provider name "nvidia   # anthropic |
            # nvidia" and blew up at provider dispatch.
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    # MUST happen before the parser is built: the --provider default reads
    # ROUTER_PROVIDER from the environment at add_argument() time. Loading .env
    # afterwards meant a ROUTER_PROVIDER set in .env was silently ignored and
    # every run quietly used the default.
    load_dotenv(REPO_ROOT / ".env")

    parser = argparse.ArgumentParser(description="WhatsApp message notification router")
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "dataset")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "output.csv")
    parser.add_argument("--media-cache", type=Path, default=CODE_DIR / "cache" / "media.json")
    parser.add_argument(
        "--provider",
        default=os.environ.get("ROUTER_PROVIDER", DEFAULT_PROVIDER),
        choices=["stub", "anthropic", "nvidia"],
        help="stub is offline and deterministic; it is the fallback that "
             "guarantees a submittable output.csv.",
    )
    parser.add_argument(
        "--safety-provider", default=os.environ.get("SAFETY_PROVIDER", "stub"),
        choices=["stub", "anthropic", "nvidia"],
        help="Reasoning engine for the safety gate. Defaults to the deterministic "
             "rules ('stub') even when --provider selects an LLM: the LLM safety "
             "classifier fails the M2 gate (6 false positives across 23 trusted "
             "senders). Override only to re-measure that.",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Route only the first N messages (smoke tests).")
    parser.add_argument("--validate-only", action="store_true",
                        help="Skip routing; just validate the existing --out file.")
    args = parser.parse_args(argv)

    # argparse does NOT validate defaults against `choices`, so a bad
    # ROUTER_PROVIDER in the environment would sail past and only fail deep in
    # provider dispatch. Check it here, where the error is legible.
    if args.provider not in {"stub", "anthropic", "nvidia"}:
        parser.error(
            f"invalid provider {args.provider!r} (from ROUTER_PROVIDER); "
            "expected one of: stub, anthropic, nvidia"
        )

    if not args.validate_only:
        # Imported here so --validate-only still works if a worker module is
        # mid-merge and not yet importable.
        from data import Dataset            # noqa: PLC0415
        from router import route_all        # noqa: PLC0415
        from writer import write_output     # noqa: PLC0415

        print(f"Loading dataset from {args.dataset} ...")
        dataset = Dataset.load(args.dataset, args.media_cache)
        messages = dataset.messages
        if args.limit:
            messages = messages[: args.limit]
        print(f"  {len(messages)} messages")

        contexts = [dataset.context_for(m) for m in messages]
        with_media = sum(1 for c in contexts if c.media and c.media.available)
        needs_media = sum(1 for c in contexts if c.message.media_type)
        print(f"  media extracted for {with_media}/{needs_media} media-bearing messages")
        if needs_media and with_media < needs_media:
            print("  NOTE: media extraction incomplete (M0 may still be running); "
                  "affected rows route on text only.")

        # M2 — blind safety gate. Runs FIRST and can force mute on its own.
        # Gated messages never reach personalization, so a trusted-sender
        # signal has no opportunity to argue a scam back down.
        from safety import gate_all              # noqa: PLC0415
        from contracts import Decision           # noqa: PLC0415

        # The safety gate deliberately does NOT follow --provider. Measured on
        # all 110 rows, the LLM classifier force-muted 44 vs 22 for rules and
        # broke assertion 3 with 6 false positives on verified, clean-domain
        # senders ("vague urgency framing", "unverified sender"). The gate's
        # whole contract is that trusted senders are never falsely muted, so it
        # stays deterministic and the LLM is used for personalization instead.
        print(f"Safety gate ({args.safety_provider}) ...")
        verdicts = gate_all(contexts, provider=args.safety_provider)
        gated = {mid: v for mid, v in verdicts.items() if v.force_mute}
        print(f"  force-muted {len(gated)}/{len(contexts)} on risk grounds")

        passthrough = [c for c in contexts if not verdicts[c.message.message_id].force_mute]

        # M3 — personalization. Only reached by messages the gate cleared, so
        # this stage never considers risk and never emits scam/spam.
        #
        # The provider selects HOW the decision is made, not WHETHER
        # personalization runs. `stub` applies the rules directly; the LLM
        # providers receive the same signals rendered into their prompt (see
        # prompts.build_user_prompt). Previously `--provider nvidia` took a
        # branch that skipped M3 altogether.
        from personalize import personalize_all       # noqa: PLC0415

        if args.provider == "stub":
            print(f"Personalizing {len(passthrough)} (rules, offline) ...")
            routed = {d.message_id: d for d in personalize_all(passthrough)}
        else:
            print(f"Personalizing {len(passthrough)} (signals + {args.provider}) ...")
            routed = {d.message_id: d for d in route_all(passthrough, provider=args.provider)}

        # M4 applies to gate-forced mutes too. They used to emit `none`, but a
        # history row the user reported or muted after is strong evidence for
        # suppressing a similar message — 22 rows were throwing that away.
        from confidence import calibrate          # noqa: PLC0415
        from evidence import select_evidence      # noqa: PLC0415
        from personalize import signals_for       # noqa: PLC0415

        decisions = []
        for c in contexts:
            mid = c.message.message_id
            if mid in gated:
                v = gated[mid]
                evidence_ids = select_evidence(c, "mute")
                decisions.append(Decision(
                    message_id=mid, action="mute", message_type=v.message_type,
                    reason=v.reason,
                    confidence=calibrate("mute", v.message_type, evidence_ids,
                                         signals=signals_for(c), gate_forced=True),
                    evidence_message_ids=evidence_ids,
                ))
            else:
                decisions.append(routed[mid])

        write_output(decisions, args.out, messages)
        print(f"Wrote {args.out}")

    # The gate. Run the validator as a subprocess exactly as a grader would,
    # so it checks the file on disk rather than our in-memory objects.
    print("Validating ...")
    result = subprocess.run(
        [sys.executable, str(CODE_DIR / "validate.py"), str(args.out)],
        cwd=REPO_ROOT,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
