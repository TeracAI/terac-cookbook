"""Explicit composition for implemented Cookbook recipes."""

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable


class DuplicateRecipeError(ValueError):
    """The composition contains more than one service for the same recipe."""


class UnknownRecipeError(ValueError):
    """A caller selected a recipe that is not in this composition."""


class RecipeBindingError(ValueError):
    """The advertised recipes and their caller tools are not a valid composition."""


@runtime_checkable
class RecipeService(Protocol):
    recipe_id: str
    name: str

    def catalog_entry(self) -> dict[str, Any]: ...

    def start(self, request: object) -> object: ...

    def inspect(self, run_id: str) -> object: ...

    def respond(self, run_id: str, response: object) -> object: ...

    def resume(self, run_id: str) -> object: ...


@runtime_checkable
class RecipeToolBinding(Protocol):
    recipe_id: str
    tool_names: tuple[str, ...]

    def bind(self, server: object, service: RecipeService) -> None: ...


class RecipeCatalog:
    """An ordered, immutable set of recipe services supplied by composition."""

    def __init__(self, services: Iterable[RecipeService]):
        entries: dict[str, RecipeService] = {}
        ordered: list[RecipeService] = []
        for service in services:
            recipe_id = getattr(service, "recipe_id", None)
            if not isinstance(recipe_id, str) or not recipe_id.strip():
                raise ValueError("Recipe services require a nonempty recipe_id")
            if recipe_id in entries:
                raise DuplicateRecipeError(f"Duplicate recipe id: {recipe_id}")
            entries[recipe_id] = service
            ordered.append(service)
        self._services = entries
        self._ordered = tuple(ordered)

    def list_recipes(self) -> list[dict[str, Any]]:
        return [service.catalog_entry() for service in self._ordered]

    @property
    def recipe_ids(self) -> tuple[str, ...]:
        return tuple(service.recipe_id for service in self._ordered)

    def get(self, recipe_id: str) -> RecipeService:
        try:
            return self._services[recipe_id]
        except KeyError as exc:
            raise UnknownRecipeError(f"Unknown recipe id: {recipe_id}") from exc
