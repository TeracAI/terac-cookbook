"""CLI adapter for the same Oracle interface used by Python callers."""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from adapters.terac_study_contract import StudyReadError, StudyWriteError
from recipes.oracle import (
    InvalidReplayCaseError,
    LiveIntegrationUnavailableError,
    LiveLinkedWorkUnavailableError,
    Oracle,
    ReplayCaseUnavailableError,
    StudyCreationUnavailableError,
)
from recipes.oracle.live_contract import load_oracle_live_binding
from runtime.json_utils import unique_json_object
from runtime.runner import CheckpointIncompatibleError

MAX_STUDY_REQUEST_BYTES = 262_144
INVALID_REQUEST_CATEGORY = "invalid_request_or_checkpoint"
_REQUEST_ERROR_CODES = {
    "A GTM brief must be text": "invalid_brief",
    "Oracle start requires a brief": "invalid_brief",
    "A nonempty Oracle brief is required": "invalid_brief",
    "A nonempty clarification answer is required": "invalid_answer",
    "Run has no pending clarification": "no_pending_clarification",
    "Linked start requires a parent and follow_up/new_query intent": "parent_required",
    "Expected a canonical run identity": "invalid_run_id",
}


def _error(payload: dict) -> int:
    print(json.dumps(payload), file=sys.stderr)
    return 2


def _is_canonical_run_id(run_id: str) -> bool:
    try:
        return str(UUID(run_id)) == run_id
    except ValueError:
        return False


def _request_error(exc: ValueError, args: argparse.Namespace) -> dict:
    code = _REQUEST_ERROR_CODES.get(str(exc))
    run_id = getattr(args, "run_id", None)
    if code is None and run_id is not None and not _is_canonical_run_id(run_id):
        code = "invalid_run_id"
    if code is None and getattr(args, "brief", None) is not None and not args.brief.strip():
        code = "invalid_brief"
    if code is None:
        return {"error": INVALID_REQUEST_CATEGORY}
    return {"error": code, "category": INVALID_REQUEST_CATEGORY}


def _storage_error(exc: OSError) -> dict:
    if isinstance(exc, (NotADirectoryError, FileExistsError)):
        return {"error": "state_dir_not_directory", "category": "checkpoint_unavailable"}
    if isinstance(exc, FileNotFoundError):
        return {"error": "checkpoint_not_found", "category": "checkpoint_unavailable"}
    return {"error": "checkpoint_unavailable"}


def _reject_nonfinite(_value: str):
    raise ValueError("invalid study request")


def _read_study_request(path: Path | None) -> dict | None:
    if path is None:
        return None
    if not path.is_absolute():
        raise ValueError("study request path must be absolute")
    try:
        content = path.read_bytes()
    except OSError:
        raise ValueError("study request is unavailable") from None
    if len(content) > MAX_STUDY_REQUEST_BYTES:
        raise ValueError("study request is too large")
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_json_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError("study request is invalid") from None
    if not isinstance(value, dict):
        raise ValueError("study request must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Oracle expert review; replay is the default. Terac study creation uses "
            "--terac-study-creation with --mode live; launch requires fresh confirmation."
        )
    )
    parser.add_argument("--state-dir", type=Path, default=Path(".tmp/oracle"))
    parser.add_argument(
        "--oracle-live-binding",
        type=Path,
        help="absolute path to one reviewed, read-only Terac study binding",
    )
    parser.add_argument(
        "--terac-study-creation",
        action="store_true",
        help="enable Oracle's explicit Terac feasibility, draft, and launch calls",
    )
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start", help="Open review, follow_up or new_query")
    start.add_argument("--brief", required=True)
    start.add_argument("--mode", choices=["replay", "live"], default="replay")
    start.add_argument("--parent-id")
    start.add_argument("--intent", choices=["review", "follow_up", "new_query"], default="review")
    start.add_argument("--replay-case")
    start.add_argument("--study-request-file", type=Path)
    for action in ["inspect", "resume", "respond"]:
        command = actions.add_parser(action)
        command.add_argument("run_id")
        if action == "respond":
            command.add_argument("--answer", required=True)
    launch = actions.add_parser("launch", help="Launch a reviewed draft with fresh confirmation")
    launch.add_argument("run_id")
    launch.add_argument("--expected-total-cost-cents", type=int, required=True)
    launch.add_argument("--maximum-total-cost-cents", type=int, required=True)
    launch.add_argument("--scope-sha256", required=True)
    launch.add_argument("--confirmation", required=True)
    args = parser.parse_args()
    if args.oracle_live_binding is not None and not args.oracle_live_binding.is_absolute():
        parser.error("--oracle-live-binding must be absolute")
    try:
        live_binding = (
            load_oracle_live_binding(args.oracle_live_binding)
            if args.oracle_live_binding is not None
            else None
        )
        study_adapter = None
        if args.terac_study_creation:
            from adapters.terac_studies import TeracStudies

            study_adapter = TeracStudies(lambda: os.environ.get("TERAC_API_KEY"))
        oracle = Oracle(args.state_dir, live_binding=live_binding, study_adapter=study_adapter)
        if args.action == "start":
            study_request = _read_study_request(args.study_request_file)
            snapshot = oracle.start(
                args.brief,
                mode=args.mode,
                parent_id=args.parent_id,
                intent=args.intent,
                replay_case=args.replay_case,
                study_request=study_request,
            )
        elif args.action == "inspect":
            snapshot = oracle.inspect(args.run_id)
        elif args.action == "respond":
            snapshot = oracle.respond(args.run_id, args.answer)
        elif args.action == "resume":
            snapshot = oracle.resume(args.run_id)
        else:
            snapshot = oracle.launch(
                args.run_id,
                expected_total_cost_cents=args.expected_total_cost_cents,
                maximum_total_cost_cents=args.maximum_total_cost_cents,
                scope_sha256=args.scope_sha256,
                confirmation=args.confirmation,
            )
    except (
        CheckpointIncompatibleError,
        InvalidReplayCaseError,
        LiveIntegrationUnavailableError,
        LiveLinkedWorkUnavailableError,
        ReplayCaseUnavailableError,
        StudyCreationUnavailableError,
        StudyWriteError,
    ) as exc:
        return _error({"error": exc.code})
    except StudyReadError as exc:
        return _error({"error": exc.code})
    except ValueError as exc:
        return _error(_request_error(exc, args))
    except OSError as exc:
        return _error(_storage_error(exc))
    print(json.dumps(snapshot.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
