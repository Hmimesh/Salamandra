from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from Item_node import Requirement, normalize_item_id


NodeKey = tuple[str, str | None, str]


@dataclass(frozen=True)
class DependencyNode:
    scope: str
    owner_user_id: str | None
    item_id: str
    requirements: tuple[Requirement, ...]
    active: bool = True

    @property
    def key(self) -> NodeKey:
        return (self.scope, self.owner_user_id, self.item_id)


def normalize_dependency_requirements(value: Any) -> tuple[Requirement, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("Inventory dependencies must be a list.")

    normalized: list[Requirement] = []
    seen: set[str] = set()
    for raw in value:
        if isinstance(raw, Requirement):
            item_id = raw.item_id
            amount_value: Any = raw.amount
        elif isinstance(raw, dict):
            item_id = raw.get("item_id", raw.get("item", ""))
            amount_value = raw.get("amount", 1)
        else:
            raise ValueError("Each inventory dependency must be an object.")

        if not isinstance(item_id, str):
            raise ValueError("Inventory dependency target is invalid.")
        target_id = normalize_item_id(item_id)
        if isinstance(amount_value, bool):
            raise ValueError("Inventory dependency quantity must be a whole number.")
        if isinstance(amount_value, int):
            amount = amount_value
        elif isinstance(amount_value, str) and amount_value.strip().isdigit():
            amount = int(amount_value.strip())
        else:
            raise ValueError("Inventory dependency quantity must be a whole number.")
        if amount <= 0 or amount > 10_000:
            raise ValueError(
                "Inventory dependency quantity must be between 1 and 10,000."
            )
        if target_id in seen:
            raise ValueError("Inventory dependencies cannot contain duplicate targets.")
        seen.add(target_id)
        normalized.append(Requirement(target_id, amount))
    return tuple(normalized)


def validate_dependency_updates(
    nodes: Iterable[DependencyNode],
    updates: dict[NodeKey, tuple[Requirement, ...]],
    *,
    removed_keys: Iterable[NodeKey] = (),
) -> None:
    graph = {node.key: node for node in nodes if node.active}
    for key in removed_keys:
        graph.pop(key, None)
    for key, requirements in updates.items():
        graph[key] = DependencyNode(*key, requirements=requirements)

    def resolve(source: DependencyNode, target_id: str) -> NodeKey | None:
        if target_id == source.item_id:
            raise ValueError("An inventory item cannot depend on itself.")
        if source.scope == "personal":
            personal_key = ("personal", source.owner_user_id, target_id)
            if personal_key in graph:
                return personal_key
        shared_key = ("shared", None, target_id)
        return shared_key if shared_key in graph else None

    visiting: set[NodeKey] = set()
    visited: set[NodeKey] = set()

    def visit(key: NodeKey) -> None:
        if key in visiting:
            raise ValueError("Inventory dependencies cannot contain a cycle.")
        if key in visited:
            return
        node = graph[key]
        visiting.add(key)
        for requirement in node.requirements:
            target_key = resolve(node, requirement.item_id)
            if target_key is None:
                raise ValueError(
                    "Dependency target must be an active inventory item visible in this scope."
                )
            visit(target_key)
        visiting.remove(key)
        visited.add(key)

    for key in updates:
        visit(key)
