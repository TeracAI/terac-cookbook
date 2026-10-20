import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from adapters.replay import ReplayAdapter
from recipes.oracle import Oracle

ROOT = Path(__file__).resolve().parents[1]
BRIEF = "Synthetic repair-shop appointment tool: demand and pricing are unproven."
PRIVATE_INPUT = "synthetic-secret-" + "do-not-echo"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def cookbook_session(state_dir, *, env=None, simulation_dir=None):
    args = ["-m", "adapters.cookbook_mcp", "--state-dir", str(state_dir)]
    if simulation_dir is not None:
        args.extend(["--simulation-dir", str(simulation_dir)])
    parameters = StdioServerParameters(
        command=sys.executable,
        args=args,
        cwd=str(ROOT),
        env=env,
    )
    with anyio.fail_after(30):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(session, name, arguments=None):
    result = await session.call_tool(name, arguments or {})
    return result, json.loads(result.content[0].text)


def publish_simulation_observation(simulation_dir, snapshot, revision, kind, **fields):
    reference = snapshot["simulation_ref"]
    observation = {
        "run_id": snapshot["run_id"],
        "reference": reference,
        "artifact_id": snapshot["artifact_id"],
        "scope": snapshot["scope"],
        "revision": revision,
        "kind": kind,
        **fields,
    }
    folder = simulation_dir / "runs" / reference.removeprefix("sim:") / "observations"
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{revision:020d}.json"
    assert not final.exists()
    temporary = final.with_suffix(".tmp")
    temporary.write_text(json.dumps(observation), encoding="utf-8")
    temporary.rename(final)
    return final


def synthetic_review(snapshot):
    return {
        "artifact_id": snapshot["artifact_id"],
        "scope": snapshot["scope"],
        "recommendation": "inconclusive",
        "rationale": "Synthetic local mailbox review.",
        "findings": [],
        "comments": [],
        "limitations": ["Synthetic rehearsal only."],
        "next_steps": [],
        "provenance": "synthetic",
    }


@pytest.mark.anyio
async def test_discovery_lists_only_oracle_and_has_no_state_or_credential_inputs(tmp_path):
    state_dir = tmp_path / "unused-state"
    async with cookbook_session(state_dir) as session:
        tools = (await session.list_tools()).tools
        assert "cookbook_list_recipes" in {tool.name for tool in tools}
        for tool in tools:
            assert tool.inputSchema["additionalProperties"] is False
            assert not {"state_dir", "credentials", "api_key", "path"} & set(
                tool.inputSchema.get("properties", {})
            )
        result, discovery = await call(session, "cookbook_list_recipes")
        assert result.isError is False
        assert len(discovery["recipes"]) == 1
        oracle = discovery["recipes"][0]
        assert oracle["id"] == "oracle"
        assert oracle["name"] == "Oracle"
        assert oracle["modes"] == ["replay"]
        assert oracle["live_available"] is False
        assert oracle["default_replay_case"] == "inconclusive"
        assert {case["id"] for case in oracle["replay_cases"]} == {
            "pass_with_comments",
            "fail",
            "inconclusive",
        }
        assert not state_dir.exists()


@pytest.mark.anyio
async def test_oracle_actions_preserve_waiting_clarification_and_delivery(tmp_path):
    async with cookbook_session(tmp_path) as session:
        assert {tool.name for tool in (await session.list_tools()).tools} == {
            "cookbook_list_recipes",
            "oracle_start",
            "oracle_inspect",
            "oracle_respond",
            "oracle_resume",
        }
        result, started = await call(session, "oracle_start", {"brief": BRIEF})
        assert result.isError is False
        assert started["mode"] == "replay"
        assert started["status"] == "working"
        assert started["phase"] == "waiting_for_expert"
        assert started["feedback"] is None
        arguments = {"run_id": started["run_id"]}
        assert (await call(session, "oracle_inspect", arguments))[1] == started

        _, pending = await call(session, "oracle_resume", arguments)
        assert pending["status"] == "needs_input"
        assert pending["question"]
        assert (await call(session, "oracle_inspect", arguments))[1] == pending
        assert (await call(session, "oracle_resume", arguments))[1] == pending

        _, answered = await call(
            session, "oracle_respond", {**arguments, "answer": "Independent repair shops"}
        )
        assert answered["status"] == "working"
        assert answered["question"] is None
        assert answered["feedback"] is None
        assert (await call(session, "oracle_inspect", arguments))[1] == answered

        _, delivered = await call(session, "oracle_resume", arguments)
        assert delivered["status"] == "succeeded"
        assert delivered["feedback"]["recommendation"] == "inconclusive"
        assert delivered["feedback"]["provenance"] == "synthetic"
        assert delivered["artifact_id"] == started["artifact_id"]
        assert (await call(session, "oracle_inspect", arguments))[1] == delivered
        assert (await call(session, "oracle_resume", arguments))[1] == delivered


@pytest.mark.anyio
async def test_invalid_tool_arguments_return_sanitized_errors_without_creating_runs(tmp_path):
    attempts = [
        ("oracle_start", {"brief": BRIEF, "state_dir": "untrusted-path"}),
        ("cookbook_list_recipes", {"api_key": PRIVATE_INPUT}),
        ("oracle_start", {}),
        ("oracle_start", {"brief": {"private": PRIVATE_INPUT}}),
        ("oracle_inspect", {"run_id": None}),
        ("oracle_inspect", {"run_id": True}),
        ("oracle_inspect", {"run_id": "../escape"}),
        ("oracle_resume", {"run_id": "not-a-run"}),
        ("oracle_respond", {"run_id": "not-a-run", "answer": "Answer"}),
        ("oracle_respond", {"run_id": "not-a-run", "answer": []}),
        ("oracle_start", {"brief": "   "}),
        ("oracle_start", {"brief": BRIEF, "intent": "follow_up"}),
        ("oracle_start", {"brief": BRIEF, "mode": "unknown"}),
        ("oracle_start", {"brief": BRIEF, "parent_id": "null"}),
        ("oracle_start", {"brief": BRIEF, "parent_id": False}),
    ]
    async with cookbook_session(tmp_path) as session:
        for name, arguments in attempts:
            result, error = await call(session, name, arguments)
            assert result.isError is True
            assert error == {"error": "invalid_request_or_checkpoint"}
            assert result.structuredContent == error
        result, error = await call(session, "unrecognized_tool", {"private": PRIVATE_INPUT})
        assert result.isError is True
        assert error == {"error": "unknown_tool"}
        assert not list(tmp_path.iterdir())


@pytest.mark.anyio
async def test_credentials_do_not_select_live_and_explicit_live_creates_no_run(tmp_path):
    async with cookbook_session(
        tmp_path, env={"TERAC_API_KEY": "synthetic-" + "unused-credential"}
    ) as session:
        result, error = await call(session, "oracle_start", {"brief": BRIEF, "mode": "live"})
        assert result.isError is True
        assert error == {"error": "live_unavailable"}
        assert not list(tmp_path.iterdir())
        result, started = await call(session, "oracle_start", {"brief": BRIEF})
        assert result.isError is False
        assert started["mode"] == "replay"
        _, discovery = await call(session, "cookbook_list_recipes")
        assert discovery["recipes"][0]["live_available"] is False


@pytest.mark.anyio
async def test_mcp_start_selects_a_case_and_preserves_its_identity(tmp_path):
    async with cookbook_session(tmp_path) as session:
        _, started = await call(
            session,
            "oracle_start",
            {"brief": BRIEF, "replay_case": "pass_with_comments"},
        )
        _, delivered = await call(session, "oracle_resume", {"run_id": started["run_id"]})
        _, explicit_null = await call(
            session, "oracle_start", {"brief": BRIEF, "replay_case": None}
        )

    assert started["replay_case"] == "pass_with_comments"
    assert delivered["feedback"]["recommendation"] == "pass"
    assert delivered["feedback"]["comments"]
    assert explicit_null["replay_case"] == "inconclusive"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("replay_case", "expected_error"),
    [
        ("unknown", "invalid_replay_case"),
        ("null", "invalid_replay_case"),
        (4, "invalid_replay_case"),
        ("custom", "replay_case_unavailable"),
    ],
)
async def test_mcp_returns_stable_selector_errors(tmp_path, replay_case, expected_error):
    async with cookbook_session(tmp_path) as session:
        result, error = await call(
            session,
            "oracle_start",
            {"brief": BRIEF, "replay_case": replay_case},
        )

    assert result.isError is True
    assert error == {"error": expected_error}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "replay_case",
    ["[]", "{}", "   ", '"pass_with_comments"'],
)
async def test_mcp_preserves_invalid_selector_strings_over_stdio(tmp_path, replay_case):
    async with cookbook_session(tmp_path) as session:
        result, error = await call(
            session,
            "oracle_start",
            {"brief": BRIEF, "replay_case": replay_case},
        )

    assert result.isError is True
    assert error == {"error": "invalid_replay_case"}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_restart_keeps_clarification_and_linked_work_preserves_prior_feedback(tmp_path):
    async with cookbook_session(tmp_path) as session:
        _, started = await call(session, "oracle_start", {"brief": BRIEF})
        arguments = {"run_id": started["run_id"]}
        _, pending = await call(session, "oracle_resume", arguments)

    async with cookbook_session(tmp_path) as session:
        assert (await call(session, "oracle_inspect", arguments))[1] == pending
        await call(session, "oracle_respond", {**arguments, "answer": "Independent repair shops"})
        _, original = await call(session, "oracle_resume", arguments)
        children = []
        for intent in ("follow_up", "new_query"):
            result, child = await call(
                session,
                "oracle_start",
                {
                    "brief": BRIEF + " Revised channel.",
                    "parent_id": original["run_id"],
                    "intent": intent,
                },
            )
            assert result.isError is False
            assert child["run_id"] != original["run_id"]
            assert child["parent_id"] == original["run_id"]
            assert child["intent"] == intent
            assert child["artifact_id"] != original["artifact_id"]
            assert child["feedback"] is None
            children.append(child)
        assert (await call(session, "oracle_inspect", arguments))[1] == original

    async with cookbook_session(tmp_path) as session:
        assert (await call(session, "oracle_inspect", arguments))[1] == original
        for child in children:
            assert (await call(session, "oracle_inspect", {"run_id": child["run_id"]}))[1] == child


@pytest.mark.anyio
async def test_inspection_reads_saved_failure_and_missing_storage_is_sanitized(tmp_path):
    oracle = Oracle(tmp_path, replay=ReplayAdapter([{"kind": "invalid"}]))
    started = oracle.start(BRIEF)
    failed = oracle.resume(started.run_id)
    assert failed.status == "failed"
    checkpoint = tmp_path / f"{failed.run_id}.json"
    original_bytes = checkpoint.read_bytes()
    async with cookbook_session(tmp_path) as session:
        result, inspected = await call(session, "oracle_inspect", {"run_id": failed.run_id})
        assert result.isError is True
        assert inspected == {"error": "checkpoint_incompatible"}
        assert checkpoint.read_bytes() == original_bytes
        result, error = await call(
            session, "oracle_inspect", {"run_id": "00000000-0000-0000-0000-000000000001"}
        )
        assert result.isError is True
        assert error == {"error": "checkpoint_unavailable"}


@pytest.mark.anyio
async def test_concurrent_responses_accept_only_one_pending_answer(tmp_path):
    async with cookbook_session(tmp_path) as session:
        _, started = await call(session, "oracle_start", {"brief": BRIEF})
        arguments = {"run_id": started["run_id"]}
        await call(session, "oracle_resume", arguments)
        responses = []

        async def answer(text):
            responses.append(await call(session, "oracle_respond", {**arguments, "answer": text}))

        async with anyio.create_task_group() as group:
            group.start_soon(answer, "First synthetic answer")
            group.start_soon(answer, "Second synthetic answer")
        assert sorted(result.isError for result, _ in responses) == [False, True]
        error = next(data for result, data in responses if result.isError)
        assert error == {"error": "invalid_request_or_checkpoint"}
        _, inspected = await call(session, "oracle_inspect", arguments)
        assert inspected["status"] == "working"
        assert inspected["feedback"] is None


@pytest.mark.anyio
async def test_invalid_responses_leave_waiting_and_pending_clarification_unchanged(tmp_path):
    async with cookbook_session(tmp_path) as session:
        _, started = await call(session, "oracle_start", {"brief": BRIEF})
        arguments = {"run_id": started["run_id"]}
        result, error = await call(session, "oracle_respond", {**arguments, "answer": "Too early"})
        assert result.isError is True
        assert error == {"error": "invalid_request_or_checkpoint"}
        assert (await call(session, "oracle_inspect", arguments))[1] == started
        _, pending = await call(session, "oracle_resume", arguments)
        result, error = await call(session, "oracle_respond", {**arguments, "answer": "   "})
        assert result.isError is True
        assert error == {"error": "invalid_request_or_checkpoint"}
        assert (await call(session, "oracle_inspect", arguments))[1] == pending


@pytest.mark.anyio
@pytest.mark.parametrize(
    "brief,answer",
    [
        ('"quoted brief"', '"quoted answer"'),
        ("null", "null"),
        ("true", "false"),
        ("123", "-1.5e2"),
        ('["synthetic"]', '["answer"]'),
        ('{"claim":"synthetic"}', '{"answer":"synthetic"}'),
        ('  "quoted brief"  ', '  "quoted answer"  '),
        ('"escaped \\n brief"', '"escaped \\t answer"'),
        ('"taller reparación"', '"revisión sintética"'),
    ],
)
async def test_json_looking_briefs_and_answers_keep_exact_python_caller_text(
    tmp_path, brief, answer
):
    python_dir = tmp_path / "python"
    mcp_dir = tmp_path / "mcp"
    oracle = Oracle(python_dir)
    python_started = oracle.start(brief)
    oracle.resume(python_started.run_id)
    oracle.respond(python_started.run_id, answer)
    python_record = json.loads(
        (python_dir / f"{python_started.run_id}.json").read_text(encoding="utf-8")
    )

    async with cookbook_session(mcp_dir) as session:
        result, started = await call(session, "oracle_start", {"brief": brief})
        assert result.isError is False
        assert started["brief"] == python_started.brief == brief
        assert started["artifact_id"] == python_started.artifact_id
        arguments = {"run_id": started["run_id"]}
        await call(session, "oracle_resume", arguments)
        result, _ = await call(session, "oracle_respond", {**arguments, "answer": answer})
        assert result.isError is False
        mcp_record = json.loads((mcp_dir / f"{started['run_id']}.json").read_text(encoding="utf-8"))
        assert mcp_record["answer"] == python_record["answer"] == answer
        result, error = await call(
            session,
            "oracle_start",
            {"brief": brief, "parent_id": json.dumps(started["run_id"]), "intent": "follow_up"},
        )
        assert result.isError is True
        assert error == {"error": "invalid_request_or_checkpoint"}
        assert len(list(mcp_dir.iterdir())) == 1


@pytest.mark.anyio
async def test_simulation_is_hidden_until_local_mailbox_is_configured(tmp_path):
    state_dir = tmp_path / "state"
    async with cookbook_session(state_dir) as session:
        _, discovery = await call(session, "cookbook_list_recipes")
        assert discovery["recipes"][0]["modes"] == ["replay"]
        result, error = await call(session, "oracle_start", {"brief": BRIEF, "mode": "simulation"})

    assert result.isError is True
    assert error == {"error": "simulation_unavailable"}
    assert not state_dir.exists()


@pytest.mark.anyio
async def test_simulation_mcp_stdio_uses_out_of_band_observations(tmp_path):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    async with cookbook_session(state_dir, simulation_dir=simulation_dir) as session:
        _, discovery = await call(session, "cookbook_list_recipes")
        assert discovery["recipes"][0]["modes"] == ["replay", "simulation"]
        assert discovery["recipes"][0]["live_available"] is False
        _, started = await call(session, "oracle_start", {"brief": BRIEF, "mode": "simulation"})
        run_args = {"run_id": started["run_id"]}
        checkpoint = state_dir / f"{started['run_id']}.json"
        original = checkpoint.read_bytes()

        assert started["simulation_ref"].startswith("sim:")
        assert started["simulation_revision"] == 0
        assert (await call(session, "oracle_inspect", run_args))[1] == started
        _, no_observation = await call(session, "oracle_resume", run_args)
        assert no_observation == started
        assert checkpoint.read_bytes() == original

        publish_simulation_observation(simulation_dir, started, 1, "waiting")
        _, waiting = await call(session, "oracle_resume", run_args)
        assert waiting["simulation_revision"] == 1
        assert (await call(session, "oracle_inspect", run_args))[1] == waiting

        publish_simulation_observation(
            simulation_dir,
            started,
            2,
            "clarification",
            question="Who is the buyer?",
        )
        assert (await call(session, "oracle_inspect", run_args))[1] == waiting
        _, pending = await call(session, "oracle_resume", run_args)
        assert pending["status"] == "needs_input"
        publish_simulation_observation(simulation_dir, started, 3, "waiting")
        before_respond = {path: path.read_bytes() for path in simulation_dir.rglob("*.json")}
        _, answered = await call(
            session,
            "oracle_respond",
            {**run_args, "answer": "Independent repair shops"},
        )
        assert answered["status"] == "working"
        assert answered["simulation_revision"] == 2
        assert {
            path: path.read_bytes() for path in simulation_dir.rglob("*.json")
        } == before_respond

        _, second_wait = await call(session, "oracle_resume", run_args)
        assert second_wait["simulation_revision"] == 3
        publish_simulation_observation(
            simulation_dir,
            started,
            4,
            "review",
            feedback=synthetic_review(started),
        )
        _, delivered = await call(session, "oracle_resume", run_args)
        assert delivered["status"] == "succeeded"
        assert delivered["simulation_revision"] == 4
        assert delivered["feedback"]["provenance"] == "synthetic"
        assert delivered["feedback"]["artifact_id"] == started["artifact_id"]
        assert delivered["feedback"]["scope"] == started["scope"]

    async with cookbook_session(state_dir, simulation_dir=simulation_dir) as session:
        assert (await call(session, "oracle_inspect", run_args))[1] == delivered
        publish_simulation_observation(simulation_dir, started, 5, "waiting")
        checkpoint = state_dir / f"{started['run_id']}.json"
        accepted_bytes = checkpoint.read_bytes()
        result, terminal = await call(session, "oracle_resume", run_args)
        assert result.isError is False
        assert terminal == delivered
        assert checkpoint.read_bytes() == accepted_bytes


@pytest.mark.anyio
async def test_invalid_simulation_mcp_observation_is_sanitized_and_atomic(tmp_path):
    state_dir = tmp_path / "state"
    simulation_dir = tmp_path / "simulation"
    async with cookbook_session(state_dir, simulation_dir=simulation_dir) as session:
        _, started = await call(session, "oracle_start", {"brief": BRIEF, "mode": "simulation"})
        observation = {
            "run_id": started["run_id"],
            "reference": started["simulation_ref"],
            "artifact_id": "0" * 64,
            "scope": started["scope"],
            "revision": 1,
            "kind": "waiting",
            "private-canary": PRIVATE_INPUT,
        }
        folder = (
            simulation_dir
            / "runs"
            / started["simulation_ref"].removeprefix("sim:")
            / "observations"
        )
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{1:020d}.json").write_text(json.dumps(observation), encoding="utf-8")
        checkpoint = state_dir / f"{started['run_id']}.json"
        original = checkpoint.read_bytes()

        result, error = await call(session, "oracle_resume", {"run_id": started["run_id"]})

    assert result.isError is True
    assert error == {"error": "simulation_observation_invalid"}
    assert PRIVATE_INPUT not in json.dumps(error)
    assert checkpoint.read_bytes() == original


def test_startup_rejects_relative_state_directory_before_creating_state():
    result = subprocess.run(
        [sys.executable, "-m", "adapters.cookbook_mcp", "--state-dir", "relative-state"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--state-dir must be absolute" in result.stderr
    assert not result.stdout
    assert not (ROOT / "relative-state").exists()
