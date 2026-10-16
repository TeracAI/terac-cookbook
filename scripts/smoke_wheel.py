"""Exercise the built, noneditable package outside its source checkout."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    if wheel.suffix != ".whl":
        parser.error("--wheel must point to a built wheel")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("TERAC_", "PYTHONPATH", "VIRTUAL_ENV"))
    }
    with tempfile.TemporaryDirectory(prefix="cookbook-installed-") as directory:
        work = Path(directory)
        installed = work / "installed"
        constraints = work / "locked-runtime.txt"
        python = installed / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        cli = installed / ("Scripts/terac-oracle.exe" if os.name == "nt" else "bin/terac-oracle")
        subprocess.run(
            [
                args.uv,
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--output-file",
                str(constraints),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            [args.uv, "venv", "--python", os.sys.executable, str(installed)],
            cwd=work,
            env=environment,
            check=True,
            timeout=60,
        )
        subprocess.run(
            [
                args.uv,
                "pip",
                "install",
                "--offline",
                "--python",
                str(python),
                "--constraint",
                str(constraints),
                str(wheel),
            ],
            cwd=work,
            env=environment,
            check=True,
            timeout=90,
        )
        state = work / "runs"
        brief = "Fictional caf\u00e9 \U0001f469\U0001f3fd\u200d\U0001f527 launch brief"

        def call(*arguments: str) -> dict:
            result = subprocess.run(
                [str(cli), "--state-dir", str(state), *arguments],
                cwd=work,
                env={**environment, "PYTHONIOENCODING": "ascii"},
                capture_output=True,
                text=True,
                check=True,
                timeout=20,
            )
            return json.loads(result.stdout)

        parent = None
        for case, verdict in (
            ("pass_with_comments", "pass"),
            ("fail", "fail"),
            ("inconclusive", "inconclusive"),
        ):
            started = call("start", "--brief", brief, "--replay-case", case)
            assert started["status"] == "working"
            assert started["brief"] == brief
            run_id = started["run_id"]
            checkpoint = state / f"{run_id}.json"
            original = checkpoint.read_bytes()
            assert call("inspect", run_id) == started
            assert checkpoint.read_bytes() == original
            progressed = call("resume", run_id)
            if case == "inconclusive":
                assert progressed["status"] == "needs_input" and progressed["question"]
                answered = call("respond", run_id, "--answer", "Independent repair shops")
                assert answered["status"] == "working" and answered["feedback"] is None
                progressed = call("resume", run_id)
            assert progressed["status"] == "succeeded"
            assert progressed["feedback"]["recommendation"] == verdict
            assert progressed["feedback"]["provenance"] == "synthetic"
            assert call("inspect", run_id) == progressed
            parent = progressed
            print(f"Installed CLI: {case} delivered {verdict}; restart and pure inspection passed")
        linked = call(
            "start",
            "--brief",
            "Fictional fresh channel follow-up",
            "--parent-id",
            parent["run_id"],
            "--intent",
            "follow_up",
            "--replay-case",
            "pass_with_comments",
        )
        assert linked["parent_id"] == parent["run_id"] and linked["feedback"] is None
        assert linked["artifact_id"] != parent["artifact_id"]
        assert call("resume", linked["run_id"])["feedback"]["recommendation"] == "pass"
        assert call("inspect", parent["run_id"]) == parent
        print("Installed CLI: fresh linked follow-up passed; prior feedback preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
