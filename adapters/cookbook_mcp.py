"""Local stdio transport for explicitly composed Cookbook recipe bindings."""

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, is_dataclass

import anyio
from jsonschema import Draft202012Validator, ValidationError, validators
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from recipes.catalog import RecipeBindingError, RecipeCatalog, RecipeToolBinding

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_STRICT_TYPE_CHECKER = Draft202012Validator.TYPE_CHECKER.redefine_many(
    {
        "integer": lambda _checker, value: type(value) is int,
        "number": lambda _checker, value: type(value) in (int, float),
    }
)
_CookbookJSONSchemaValidator = validators.extend(
    Draft202012Validator, type_checker=_STRICT_TYPE_CHECKER
)


class _CookbookFunctionMetadata(FuncMetadata):
    """Avoid SDK JSON pre-parsing before typed argument validation."""

    def pre_parse_json(self, data: dict) -> dict:
        return data


def tool_result(data: dict, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data))],
        structuredContent=data,
        isError=is_error,
    )


class CookbookMCP(FastMCP):
    """Validate raw JSON types, preserve literal strings, and sanitize tool errors."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._registered_tool_names: set[str] = set()
        self._invalid_input_errors: dict[str, Callable[[dict], str | None]] = {}

    @property
    def registered_tool_names(self) -> frozenset[str]:
        return frozenset(self._registered_tool_names)

    def add_tool(self, fn, name=None, **kwargs):
        tool_name = name or fn.__name__
        if tool_name in self._registered_tool_names:
            raise RecipeBindingError(f"Duplicate tool name: {tool_name}")
        super().add_tool(fn, name=tool_name, **kwargs)
        tool = self._tool_manager.get_tool(tool_name)
        if tool is None:
            raise RecipeBindingError(f"Tool registration failed: {tool_name}")
        metadata = tool.fn_metadata
        tool.fn_metadata = _CookbookFunctionMetadata(
            arg_model=metadata.arg_model,
            output_schema=metadata.output_schema,
            output_model=metadata.output_model,
            wrap_output=metadata.wrap_output,
        )
        self._registered_tool_names.add(tool_name)

    def set_invalid_input_error(
        self, tool_name: str, error_for: Callable[[dict], str | None]
    ) -> None:
        if tool_name not in self._registered_tool_names:
            raise RecipeBindingError(f"Cannot configure an unregistered tool: {tool_name}")
        if tool_name in self._invalid_input_errors:
            raise RecipeBindingError(f"Duplicate input error policy: {tool_name}")
        self._invalid_input_errors[tool_name] = error_for

    def _invalid_input_result(self, tool_name: str, arguments: dict) -> CallToolResult:
        error_for = self._invalid_input_errors.get(tool_name)
        code = error_for(arguments) if error_for is not None else None
        if isinstance(code, str) and _ERROR_CODE.fullmatch(code):
            return tool_result({"error": code}, is_error=True)
        return tool_result({"error": "invalid_request_or_checkpoint"}, is_error=True)

    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            tool.inputSchema["additionalProperties"] = False
        return tools

    async def call_tool(self, name: str, arguments: dict):
        tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
        if tool is None:
            return tool_result({"error": "unknown_tool"}, is_error=True)
        if not isinstance(arguments, dict):
            return self._invalid_input_result(name, {})
        try:
            _CookbookJSONSchemaValidator(tool.inputSchema).validate(arguments)
        except ValidationError:
            return self._invalid_input_result(name, arguments)
        try:
            return await super().call_tool(name, arguments)
        except ToolError:
            return tool_result({"error": "invalid_request_or_checkpoint"}, is_error=True)

    async def invoke(
        self,
        operation: Callable[[], object],
        *,
        invalid_error: str = "invalid_request_or_checkpoint",
        error_for: Callable[[Exception], str | None] | None = None,
    ):
        async with self.run_lock:
            try:
                result = await anyio.to_thread.run_sync(operation)
                if hasattr(result, "model_dump"):
                    data = result.model_dump(mode="json")
                elif is_dataclass(result):
                    data = asdict(result)
                elif isinstance(result, dict):
                    data = result
                else:
                    raise ValueError("Recipe result must be a JSON object")
                return tool_result(data)
            except OSError:
                return tool_result({"error": "checkpoint_unavailable"}, is_error=True)
            except Exception as exc:
                code = error_for(exc) if error_for is not None else None
                if isinstance(code, str) and _ERROR_CODE.fullmatch(code):
                    return tool_result({"error": code}, is_error=True)
                return tool_result({"error": invalid_error}, is_error=True)


def _validate_bindings(
    catalog: RecipeCatalog,
    bindings: Iterable[RecipeToolBinding],
) -> tuple[RecipeToolBinding, ...]:
    entries = tuple(bindings)
    recipe_ids = set(catalog.recipe_ids)
    by_recipe: dict[str, RecipeToolBinding] = {}
    all_tool_names = {"cookbook_list_recipes"}
    for binding in entries:
        recipe_id = getattr(binding, "recipe_id", None)
        if not isinstance(recipe_id, str) or recipe_id not in recipe_ids:
            raise RecipeBindingError("Unknown recipe binding")
        if recipe_id in by_recipe:
            raise RecipeBindingError(f"Duplicate binding for recipe: {recipe_id}")
        names = getattr(binding, "tool_names", None)
        if not isinstance(names, tuple) or not names:
            raise RecipeBindingError(f"Recipe binding has no tool names: {recipe_id}")
        for name in names:
            if not isinstance(name, str) or not name:
                raise RecipeBindingError("Tool names must be nonempty strings")
            if name in all_tool_names:
                raise RecipeBindingError(f"Duplicate tool name: {name}")
            all_tool_names.add(name)
        by_recipe[recipe_id] = binding
    missing = recipe_ids - set(by_recipe)
    if missing:
        raise RecipeBindingError("Every recipe must have exactly one tool binding")
    return tuple(by_recipe[recipe_id] for recipe_id in catalog.recipe_ids)


def create_server(
    *,
    catalog: RecipeCatalog,
    bindings: Iterable[RecipeToolBinding],
) -> CookbookMCP:
    resolved_bindings = _validate_bindings(catalog, bindings)
    server = CookbookMCP("Terac Cookbook", log_level="ERROR")
    server.catalog = catalog
    server.run_lock = anyio.Lock()

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
    async def cookbook_list_recipes() -> dict:
        """List the implemented recipe services in this composition."""
        return {"recipes": server.catalog.list_recipes()}

    for binding in resolved_bindings:
        service = catalog.get(binding.recipe_id)
        before = server.registered_tool_names
        binding.bind(server, service)
        registered = server.registered_tool_names - before
        if registered != set(binding.tool_names):
            raise RecipeBindingError(
                f"Binding {binding.recipe_id} registered tools that differ from its declaration"
            )
    return server
