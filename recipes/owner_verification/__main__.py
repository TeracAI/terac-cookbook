"""CLI adapter for the same owner verification interface used by Python callers."""

import argparse
import json
from pathlib import Path

from recipes.owner_verification import OwnerVerification, OwnerVerificationError
from runtime.runner import CheckpointIncompatibleError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Owner verification replay: a reviewer verdict on a redacted owner call."
    )
    parser.add_argument("--state-dir", type=Path, default=Path(".tmp/owner_verification"))
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", help="Start a replay review of a summary")
    start.add_argument("--summary", required=True)
    start.add_argument(
        "--consent-recorded",
        action="store_true",
        help="assert the owner consented on the record to review",
    )
    start.add_argument("--replay-case")
    for action in ["inspect", "resume"]:
        actions.add_parser(action).add_argument("run_id")
    args = parser.parse_args()
    recipe = OwnerVerification(args.state_dir)
    try:
        if args.action == "start":
            snapshot = recipe.start(
                args.summary,
                consent_recorded=args.consent_recorded,
                replay_case=args.replay_case,
            )
        elif args.action == "inspect":
            snapshot = recipe.inspect(args.run_id)
        else:
            snapshot = recipe.resume(args.run_id)
    except (OwnerVerificationError, CheckpointIncompatibleError) as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    except (FileNotFoundError, ValueError):
        print(json.dumps({"error": "invalid_request_or_checkpoint"}))
        return 2
    print(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
