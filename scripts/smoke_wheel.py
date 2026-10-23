"""Exercise the built, noneditable package outside its source checkout."""

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path


async def smoke_mcp(python: Path, work: Path, environment: dict[str, str]) -> None:
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    state = work / "mcp runs \u03a9"
    parameters = StdioServerParameters(
        command=str(python),
        args=["-m", "cookbook_app", "--state-dir", str(state)],
        cwd=str(work),
        env=environment,
    )

    @asynccontextmanager
    async def session():
        with anyio.fail_after(30):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    yield client

    async def oracle_call(client, name: str, arguments: dict) -> dict:
        result = await client.call_tool(name, arguments)
        assert result.isError is False
        assert len(result.content) == 1 and result.content[0].type == "text"
        snapshot = json.loads(result.content[0].text)
        assert result.structuredContent == snapshot
        return snapshot

    async def pure_inspect(client, snapshot: dict) -> None:
        checkpoint = state / f"{snapshot['run_id']}.json"
        before = checkpoint.read_bytes()
        assert (
            await oracle_call(client, "oracle_inspect", {"run_id": snapshot["run_id"]}) == snapshot
        )
        assert checkpoint.read_bytes() == before

    async with session() as client:
        assert {tool.name for tool in (await client.list_tools()).tools} == {
            "cookbook_list_recipes",
            "oracle_start",
            "oracle_inspect",
            "oracle_respond",
            "oracle_resume",
        }
        catalog_result = await client.call_tool("cookbook_list_recipes", {})
        assert catalog_result.isError is False
        assert len(catalog_result.content) == 1 and catalog_result.content[0].type == "text"
        recipes = json.loads(catalog_result.content[0].text)["recipes"]
        assert len(recipes) == 1
        assert recipes[0]["id"] == "oracle" and recipes[0]["name"] == "Oracle"
        assert recipes[0]["modes"] == ["replay"] and recipes[0]["live_available"] is False
        assert recipes[0]["default_replay_case"] == "inconclusive"
        assert not state.exists()

        brief = "Fictional caf\u00e9 \U0001f469\U0001f3fd\u200d\U0001f527 launch brief"
        started = await oracle_call(client, "oracle_start", {"brief": brief})
        assert started["brief"] == brief and started["mode"] == "replay"
        assert started["replay_case"] == "inconclusive"
        assert started["status"] == "working" and started["phase"] == "waiting_for_expert"
        assert started["feedback"] is None
        await pure_inspect(client, started)
        arguments = {"run_id": started["run_id"]}
        pending = await oracle_call(client, "oracle_resume", arguments)
        assert pending["status"] == "needs_input" and pending["question"]
        await pure_inspect(client, pending)
        answered = await oracle_call(
            client,
            "oracle_respond",
            {**arguments, "answer": "Independent caf\u00e9 repair shops \U0001f527"},
        )
        assert answered["status"] == "working" and answered["question"] is None
        assert answered["brief"] == brief and answered["feedback"] is None
        await pure_inspect(client, answered)

    async with session() as client:
        await pure_inspect(client, answered)
        delivered = await oracle_call(client, "oracle_resume", arguments)
        assert delivered["status"] == "succeeded"
        assert delivered["feedback"]["recommendation"] == "inconclusive"
        assert delivered["feedback"]["provenance"] == "synthetic"
        assert delivered["artifact_id"] == started["artifact_id"]
        await pure_inspect(client, delivered)
        parent_checkpoint = state / f"{delivered['run_id']}.json"
        parent_bytes = parent_checkpoint.read_bytes()
        linked = await oracle_call(
            client,
            "oracle_start",
            {
                "brief": "Fictional fresh channel follow-up",
                "parent_id": delivered["run_id"],
                "intent": "follow_up",
                "replay_case": "pass_with_comments",
            },
        )
        assert linked["run_id"] != delivered["run_id"]
        assert linked["parent_id"] == delivered["run_id"] and linked["intent"] == "follow_up"
        assert linked["replay_case"] == "pass_with_comments" and linked["status"] == "working"
        assert linked["feedback"] is None and linked["artifact_id"] != delivered["artifact_id"]
        child = await oracle_call(client, "oracle_resume", {"run_id": linked["run_id"]})
        assert child["status"] == "succeeded" and child["feedback"]["recommendation"] == "pass"
        assert child["feedback"]["provenance"] == "synthetic"
        assert child["feedback"]["artifact_id"] == linked["artifact_id"]
        await pure_inspect(client, child)
        await pure_inspect(client, delivered)
        assert parent_checkpoint.read_bytes() == parent_bytes
    print(
        "Installed MCP: discovery, Unicode clarification, restart and fresh linked follow-up passed"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel", type=Path, help="built wheel (defaults to the sole wheel in dist/)"
    )
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--with-mcp", action="store_true", help="also rehearse installed stdio MCP")
    args = parser.parse_args()
    if args.wheel is None:
        wheels = list((Path(__file__).resolve().parents[1] / "dist").glob("*.whl"))
        if len(wheels) != 1:
            parser.error("expected exactly one wheel under dist/ or pass --wheel")
        wheel = wheels[0].resolve(strict=True)
    else:
        wheel = args.wheel.resolve(strict=True)
    if wheel.suffix != ".whl":
        parser.error("--wheel must point to a built wheel")
    stripped_prefixes = (
        "TERAC_",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(stripped_prefixes)
    }
    with tempfile.TemporaryDirectory(
        prefix="cookbook-installed-", ignore_cleanup_errors=os.name == "nt"
    ) as directory:
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
                *(["--extra", "connectors"] if args.with_mcp else []),
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
                *(["--link-mode", "copy"] if args.with_mcp else []),
                "--python",
                str(python),
                "--constraint",
                str(constraints),
                f"{wheel}[connectors]" if args.with_mcp else str(wheel),
            ],
            cwd=work,
            env=environment,
            check=True,
            timeout=90,
        )
        if not args.with_mcp:
            subprocess.run(
                [
                    str(python),
                    "-c",
                    "import importlib.util, sys; "
                    "sys.exit(importlib.util.find_spec('mcp') is not None)",
                ],
                cwd=work,
                env=environment,
                check=True,
                timeout=20,
            )
        state = work / "runs with \u03a9"
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
        if args.with_mcp:
            asyncio.run(smoke_mcp(python, work, environment))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
