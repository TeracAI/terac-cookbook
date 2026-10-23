"""CLI adapter for the same Oracle interface used by Python callers."""

import argparse
import json
from pathlib import Path

from adapters.terac_study_contract import StudyReadError
from recipes.oracle import (
    InvalidReplayCaseError,
    LiveIntegrationUnavailableError,
    LiveLinkedWorkUnavailableError,
    Oracle,
    ReplayCaseUnavailableError,
)
from recipes.oracle.live_contract import load_oracle_live_binding
from runtime.runner import CheckpointIncompatibleError


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic offline Oracle expert review")
    parser.add_argument("--state-dir", type=Path, default=Path(".tmp/oracle"))
    parser.add_argument(
        "--oracle-live-binding",
        type=Path,
        help="absolute path to one reviewed, read-only Terac study binding",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", help="Open review, follow_up or new_query")
    start.add_argument("--brief", required=True)
    start.add_argument("--mode", choices=["replay", "live"], default="replay")
    start.add_argument("--parent-id")
    start.add_argument("--intent", choices=["review", "follow_up", "new_query"], default="review")
    start.add_argument("--replay-case")
    for action in ["inspect", "resume", "respond"]:
        command = actions.add_parser(action)
        command.add_argument("run_id")
        if action == "respond":
            command.add_argument("--answer", required=True)
    args = parser.parse_args()
    if args.oracle_live_binding is not None and not args.oracle_live_binding.is_absolute():
        parser.error("--oracle-live-binding must be absolute")
    try:
        live_binding = (
            load_oracle_live_binding(args.oracle_live_binding)
            if args.oracle_live_binding is not None
            else None
        )
        oracle = Oracle(args.state_dir, live_binding=live_binding)
        if args.action == "start":
            snapshot = oracle.start(
                args.brief,
                mode=args.mode,
                parent_id=args.parent_id,
                intent=args.intent,
                replay_case=args.replay_case,
            )
        elif args.action == "inspect":
            snapshot = oracle.inspect(args.run_id)
        elif args.action == "respond":
            snapshot = oracle.respond(args.run_id, args.answer)
        else:
            snapshot = oracle.resume(args.run_id)
    except (
        CheckpointIncompatibleError,
        InvalidReplayCaseError,
        LiveIntegrationUnavailableError,
        LiveLinkedWorkUnavailableError,
        ReplayCaseUnavailableError,
    ) as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    except StudyReadError as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    except ValueError:
        print(json.dumps({"error": "invalid_request_or_checkpoint"}))
        return 2
    except OSError:
        print(json.dumps({"error": "checkpoint_unavailable"}))
        return 2
    print(json.dumps(snapshot.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
