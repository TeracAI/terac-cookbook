import hashlib
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from adapters.cookbook_mcp import CookbookMCP, create_server
from adapters.replay import ReplayAdapter
from recipes.catalog import RecipeCatalog
from recipes.oracle import Oracle
from tests.recipe_mcp_fixture import SumMCPBinding
from tests.test_recipe_catalog import SumRecipe

ROOT = Path(__file__).resolve().parents[1]
BRIEF = "Synthetic repair-shop appointment tool: demand and pricing are unproven."
PRIVATE_INPUT = "synthetic-secret-" + "do-not-echo"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def cookbook_session(state_dir, *, env=None, live_binding_path=None):
    args = ["-m", "cookbook_app", "--state-dir", str(state_dir)]
    if live_binding_path is not None:
        args.extend(["--oracle-live-binding", str(live_binding_path)])
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


@asynccontextmanager
async def multi_recipe_session(state_dir):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tests.recipe_mcp_fixture", "--state-dir", str(state_dir)],
        cwd=str(ROOT),
    )
    with anyio.fail_after(30):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(session, name, arguments=None):
    result = await session.call_tool(name, arguments or {})
    return result, json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_second_recipe_uses_shared_stdio_wire_and_restarts_with_opaque_checkpoint(tmp_path):
    async with multi_recipe_session(tmp_path) as session:
        tool_names = {tool.name for tool in (await session.list_tools()).tools}
        assert tool_names == {
            "cookbook_list_recipes",
            "oracle_start",
            "oracle_inspect",
            "oracle_respond",
            "oracle_resume",
            "oracle_launch",
            "sum_start",
            "sum_inspect",
            "sum_respond",
            "sum_resume",
            "sum_echo",
        }
        _, discovery = await call(session, "cookbook_list_recipes")
        assert [entry["id"] for entry in discovery["recipes"]] == ["oracle", "sum"]

        echo_result, echoed = await call(session, "sum_echo", {"value": "null"})
        assert echo_result.isError is False
        assert echoed == {"value": "null"}

        result, started = await call(session, "sum_start", {"values": [4, 7, 11]})
        assert result.structuredContent == started
        assert started["status"] == "working"
        assert started["phase"] == "awaiting_measurements"
        arguments = {"run_id": started["run_id"]}
        assert (await call(session, "sum_inspect", arguments))[1] == started
        _, delivered = await call(session, "sum_resume", arguments)
        assert delivered["phase"] == "aggregate_ready"
        assert delivered["result"] == {"count": 3, "sum": 22}
        assert not {"brief", "scope", "feedback", "recommendation"} & set(delivered)
        checkpoint = json.loads((tmp_path / f"{started['run_id']}.json").read_text())
        assert checkpoint["recipe_id"] == "sum"
        assert checkpoint["recipe_payload"] == {
            "values": [4, 7, 11],
            "artifact_id": "2a58a4fae15f7bf164155763b94a1cfdcc0ddbaf6de6add64b29a787f27aad32",
            "result": {"count": 3, "sum": 22},
        }
        assert not {"brief", "scope", "feedback", "recommendation"} & set(checkpoint)

        count_before = len(list(tmp_path.glob("*.json")))
        for invalid in (
            {"values": [1, "2"]},
            {"values": [1.0, 2]},
            {"values": [1, 2], "path": PRIVATE_INPUT},
        ):
            result, error = await call(session, "sum_start", invalid)
            assert result.isError is True
            assert error == {"error": "invalid_request_or_checkpoint"}
        assert len(list(tmp_path.glob("*.json"))) == count_before

        result, error = await call(session, "sum_start", {"values": []})
        assert result.isError is True
        assert error == {"error": "sum_invalid_values"}
        assert len(list(tmp_path.glob("*.json"))) == count_before

        result, error = await call(
            session, "sum_respond", {"run_id": started["run_id"], "response": "x"}
        )
        assert result.isError is True
        assert error == {"error": "invalid_request_or_checkpoint"}

    async with multi_recipe_session(tmp_path) as restarted:
        assert (await call(restarted, "sum_inspect", arguments))[1] == delivered


@pytest.mark.anyio
async def test_sum_only_catalog_has_a_complete_binding_without_oracle(tmp_path):
    catalog = RecipeCatalog((SumRecipe(tmp_path),))
    server = create_server(
        catalog=catalog,
        bindings=(SumMCPBinding(),),
    )

    names = {tool.name for tool in await server.list_tools()}
    assert names == {
        "cookbook_list_recipes",
        "sum_start",
        "sum_inspect",
        "sum_respond",
        "sum_resume",
        "sum_echo",
    }


@pytest.mark.anyio
async def test_mcp_preserves_typed_arguments_and_schema_constrained_strings():
    class Request(BaseModel):
        count: int
        label: str

    class IntegerCase(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: Literal["integer"]
        count: int

    class NumberCase(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: Literal["number"]
        count: float

    AtLeastTen = Annotated[int, Field(ge=10)]
    AtMostZero = Annotated[float, Field(le=0)]
    ShortText = Annotated[str, StringConstraints(min_length=1, max_length=4)]
    server = CookbookMCP("typed-tools")
    calls = {"map": 0, "typed": 0, "union": 0, "bounded": 0}

    @server.tool()
    async def typed_echo(request: Request) -> dict:
        calls["typed"] += 1
        return {"count": request.count, "label": request.label}

    @server.tool()
    async def typed_union(
        value: Annotated[IntegerCase | NumberCase, Field(discriminator="kind")],
    ) -> dict:
        calls["union"] += 1
        return {"case": type(value).__name__, "count": value.count}

    @server.tool()
    async def bounded_union(value: AtLeastTen | AtMostZero) -> dict:
        calls["bounded"] += 1
        return {"type": type(value).__name__, "value": value}

    @server.tool()
    async def literal_echo(value: Literal["true", "1", "null", "normal"]) -> dict:
        return {"value": value}

    @server.tool()
    async def short_echo(value: ShortText) -> dict:
        return {"value": value}

    @server.tool()
    async def integer_map(values: dict[str, int]) -> dict:
        calls["map"] += 1
        return values

    typed = await server.call_tool("typed_echo", {"request": {"count": 1, "label": "null"}})
    assert json.loads(typed[0].text) == {"count": 1, "label": "null"}

    integer_case = await server.call_tool("typed_union", {"value": {"kind": "integer", "count": 1}})
    assert not getattr(integer_case, "isError", False)
    assert json.loads(integer_case[0].text) == {"case": "IntegerCase", "count": 1}
    number_case = await server.call_tool("typed_union", {"value": {"kind": "number", "count": 1.5}})
    assert not getattr(number_case, "isError", False)
    assert json.loads(number_case[0].text) == {"case": "NumberCase", "count": 1.5}
    coerced_union = await server.call_tool(
        "typed_union", {"value": {"kind": "integer", "count": 1.0}}
    )
    assert coerced_union.isError is True

    at_least_ten = await server.call_tool("bounded_union", {"value": 10})
    assert json.loads(at_least_ten[0].text) == {"type": "int", "value": 10}
    at_most_zero = await server.call_tool("bounded_union", {"value": -0.5})
    assert json.loads(at_most_zero[0].text) == {"type": "float", "value": -0.5}
    wrong_branch = await server.call_tool("bounded_union", {"value": 10.0})
    assert getattr(wrong_branch, "isError", False) is True

    for value in ("true", "1", "null", "normal"):
        literal = await server.call_tool("literal_echo", {"value": value})
        assert json.loads(literal[0].text) == {"value": value}

    for value in ("1", "null", "true", "test"):
        short = await server.call_tool("short_echo", {"value": value})
        assert json.loads(short[0].text) == {"value": value}

    too_long = await server.call_tool("short_echo", {"value": "hello"})
    assert too_long.isError is True

    invalid_request = await server.call_tool(
        "typed_echo", {"request": {"count": 1.0, "label": "null"}}
    )
    assert invalid_request.isError is True
    assert calls["typed"] == 1
    assert calls["union"] == 2
    assert calls["bounded"] == 2

    invalid = await server.call_tool("integer_map", {"values": {"one": 1.0}})
    assert invalid.isError is True
    assert json.loads(invalid.content[0].text) == {"error": "invalid_request_or_checkpoint"}
    assert calls["map"] == 0


@pytest.mark.anyio
async def test_recipe_discovery_advertises_read_only_local_tool(tmp_path):
    server = create_server(catalog=RecipeCatalog(()), bindings=())
    tool = next(tool for tool in await server.list_tools() if tool.name == "cookbook_list_recipes")
    result = await server.call_tool("cookbook_list_recipes", {})

    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.openWorldHint is False
    assert json.loads(result[0].text) == {"recipes": []}


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
            "oracle_launch",
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
async def test_unsupported_execution_mode_is_rejected_without_creating_a_run(tmp_path):
    async with cookbook_session(tmp_path) as session:
        result, error = await call(session, "oracle_start", {"brief": BRIEF, "mode": "unsupported"})

    assert result.isError is True
    assert error == {"error": "invalid_request_or_checkpoint"}
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.anyio
async def test_credentials_do_not_select_live_and_explicit_live_creates_no_run(tmp_path):
    async with cookbook_session(
        tmp_path, env={"TERAC_API_KEY": "synthetic-" + "unused-credential"}
    ) as session:
        result, error = await call(session, "oracle_start", {"brief": BRIEF, "mode": "live"})
        assert result.isError is True
        assert error == {"error": "live_unavailable"}
        assert not list(tmp_path.iterdir())
        result, error = await call(
            session,
            "oracle_start",
            {
                "brief": BRIEF,
                "mode": "live",
                "parent_id": "not-a-uuid",
                "intent": "follow_up",
            },
        )
        assert result.isError is True
        assert error == {"error": "live_unavailable"}
        assert not list(tmp_path.iterdir())
        result, started = await call(session, "oracle_start", {"brief": BRIEF})
        assert result.isError is False
        assert started["mode"] == "replay"
        _, discovery = await call(session, "cookbook_list_recipes")
        assert discovery["recipes"][0]["live_available"] is False


def _write_live_binding(path):
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "template_version": 1,
                "read_only": True,
                "organization_id": "synthetic-org",
                "project_id": "synthetic-project",
                "opportunity_id": "synthetic-study",
                "brief_sha256": hashlib.sha256(BRIEF.encode()).hexdigest(),
                "definition_sha256": "d" * 64,
            }
        ),
        encoding="utf-8",
    )


def _credential_free_environment():
    return {
        name: os.environ[name] for name in ("PATH", "SYSTEMROOT", "WINDIR") if name in os.environ
    }


@pytest.mark.anyio
async def test_configured_live_mcp_attachment_is_inspectable_and_refresh_is_explicit(tmp_path):
    binding_path = tmp_path / "reviewed-binding.json"
    _write_live_binding(binding_path)
    state_dir = tmp_path / "state"

    async with cookbook_session(
        state_dir,
        env=_credential_free_environment(),
        live_binding_path=binding_path,
    ) as session:
        _, discovery = await call(session, "cookbook_list_recipes")
        assert discovery["recipes"][0]["modes"] == ["replay", "live"]
        assert discovery["recipes"][0]["live_available"] is True
        assert "no launch or expert feedback" in discovery["recipes"][0]["live_limit"]

        result, attached = await call(session, "oracle_start", {"brief": BRIEF, "mode": "live"})
        assert result.isError is False
        assert attached["mode"] == "live"
        assert attached["phase"] == "awaiting_provider_refresh"
        assert attached["provider_observation"] is None
        assert "synthetic-study" not in json.dumps(attached)
        checkpoint = state_dir / f"{attached['run_id']}.json"
        original = checkpoint.read_bytes()
        inspected = await call(session, "oracle_inspect", {"run_id": attached["run_id"]})
        assert inspected[1] == attached
        result, error = await call(session, "oracle_resume", {"run_id": attached["run_id"]})
        assert result.isError is True
        assert error == {"error": "credential_unavailable"}
        assert checkpoint.read_bytes() == original


@pytest.mark.anyio
async def test_configured_live_invalid_start_reports_request_error(tmp_path):
    binding_path = tmp_path / "reviewed-binding.json"
    _write_live_binding(binding_path)
    state_dir = tmp_path / "state"

    async with cookbook_session(
        state_dir,
        env=_credential_free_environment(),
        live_binding_path=binding_path,
    ) as session:
        result, error = await call(
            session,
            "oracle_start",
            {"brief": BRIEF, "mode": "live", "replay_case": "fail"},
        )

    assert result.isError is True
    assert error == {"error": "invalid_request_or_checkpoint"}
    assert not list(state_dir.glob("*.json"))


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
        assert (
            mcp_record["recipe_payload"]["answer"]
            == python_record["recipe_payload"]["answer"]
            == answer
        )
        result, error = await call(
            session,
            "oracle_start",
            {"brief": brief, "parent_id": json.dumps(started["run_id"]), "intent": "follow_up"},
        )
        assert result.isError is True
        assert error == {"error": "invalid_request_or_checkpoint"}
        assert len(list(mcp_dir.iterdir())) == 1


def test_startup_rejects_relative_state_directory_before_creating_state():
    result = subprocess.run(
        [sys.executable, "-m", "cookbook_app", "--state-dir", "relative-state"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--state-dir must be absolute" in result.stderr
    assert not result.stdout
    assert not (ROOT / "relative-state").exists()
