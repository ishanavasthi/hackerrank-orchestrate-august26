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
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--limit", type=int, default=None,
                        help="Route only the first N messages (smoke tests).")
    parser.add_argument("--validate-only", action="store_true",
                        help="Skip routing; just validate the existing --out file.")
    args = parser.parse_args(argv)

    load_dotenv(REPO_ROOT / ".env")

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

        print("Safety gate ...")
        verdicts = gate_all(contexts, provider=args.provider)
        gated = {mid: v for mid, v in verdicts.items() if v.force_mute}
        print(f"  force-muted {len(gated)}/{len(contexts)} on risk grounds")

        passthrough = [c for c in contexts if not verdicts[c.message.message_id].force_mute]
        print(f"Routing {len(passthrough)} via provider={args.provider} ...")
        routed = {d.message_id: d for d in route_all(passthrough, provider=args.provider)}

        decisions = []
        for c in contexts:
            mid = c.message.message_id
            if mid in gated:
                v = gated[mid]
                decisions.append(Decision(
                    message_id=mid, action="mute", message_type=v.message_type,
                    reason=v.reason, confidence=v.confidence,
                    evidence_message_ids=[],
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
