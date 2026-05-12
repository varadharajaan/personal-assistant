"""Configured OpenClaw prompt recipes for common laptop workflows."""

from __future__ import annotations

from dataclasses import dataclass

from .config import get_table


@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    prompt: str


def _recipe_tables() -> dict[str, dict[str, object]]:
    recipes = dict(get_table("recipes"))
    recipes.pop("execution", None)
    return {
        name: value
        for name, value in recipes.items()
        if isinstance(value, dict)
    }


def list_recipes() -> list[Recipe]:
    configured = _recipe_tables()
    return [
        Recipe(
            name=name,
            description=str(configured[name].get("description", "")),
            prompt=str(configured[name].get("prompt", "")),
        )
        for name in sorted(configured)
    ]


def get_recipe(name: str) -> Recipe:
    configured = _recipe_tables()
    if name not in configured:
        available = ", ".join(sorted(configured))
        raise ValueError(f"Unknown recipe '{name}'. Available: {available}")
    item = configured[name]
    return Recipe(
        name=name,
        description=str(item.get("description", "")),
        prompt=str(item.get("prompt", "")),
    )
