"""Configured OpenClaw agent role routing."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .config import get_bool, get_int, get_list, get_path, get_str, get_table
from .openclaw_runner import CommandResult, OpenClawRunner
from .paths import OPENCLAW_WORKSPACE_DIR, ensure_runtime_dirs


@dataclass(frozen=True)
class AgentRole:
    name: str
    description: str
    agent: str
    model: str
    workspace: Path
    thinking: str | None
    ensure_agent: bool
    use_model_override: bool


@dataclass(frozen=True)
class ResolvedAgentCall:
    role: AgentRole
    agent: str
    model: str | None
    thinking: str | None


@dataclass(frozen=True)
class AgentEnsureResult:
    role: str
    agent: str
    workspace: str
    existed: bool
    created: bool
    model_checked: bool
    model_repaired: bool
    returncode: int
    message: str


def configured_roles() -> list[AgentRole]:
    routing = get_table("agent.routing")
    roles = routing.get("roles", [])
    if not isinstance(roles, list):
        raise TypeError("agent.routing.roles must be a list")

    configured: list[AgentRole] = []
    for raw in roles:
        if not isinstance(raw, dict):
            continue
        configured.append(
            AgentRole(
                name=str(raw.get("name", "")).strip(),
                description=str(raw.get("description", "")).strip(),
                agent=str(raw.get("agent", "")).strip(),
                model=str(raw.get("model", "")).strip(),
                workspace=Path(str(raw.get("workspace", ""))).expanduser().resolve(),
                thinking=str(raw.get("thinking", "")).strip() or None,  # type: ignore[arg-type]
                ensure_agent=bool(raw.get("ensure_agent", False)),
                use_model_override=bool(raw.get("use_model_override", False)),
            )
        )
    return [role for role in configured if role.name and role.agent]


def role_names() -> list[str]:
    names = [role.name for role in configured_roles()]
    configured_choices = [str(item) for item in get_list("agent.routing.role_choices")]
    return [name for name in configured_choices if name in names]


def get_role(name: str | None = None) -> AgentRole:
    selected = name or get_str("agent.routing.default_role")
    roles = {role.name: role for role in configured_roles()}
    if selected not in roles:
        available = ", ".join(sorted(roles))
        raise ValueError(f"Unknown agent role '{selected}'. Available: {available}")
    return roles[selected]


def resolve_agent_call(
    *,
    role_name: str | None,
    explicit_agent: str | None,
    explicit_model: str | None,
    explicit_thinking: str | None,
) -> ResolvedAgentCall:
    role = get_role(role_name)
    return ResolvedAgentCall(
        role=role,
        agent=(explicit_agent or role.agent),
        model=(explicit_model or (role.model if role.use_model_override else None)),
        thinking=(explicit_thinking or role.thinking or None),
    )


def roles_as_dicts(roles: Iterable[AgentRole] | None = None) -> list[dict[str, object]]:
    return [
        {**asdict(role), "workspace": str(role.workspace)}
        for role in (roles if roles is not None else configured_roles())
    ]


def _actual_agents(runner: OpenClawRunner, timeout_seconds: int | None) -> dict[str, dict[str, object]]:
    result = runner.run(["agents", "list", "--json"], timeout_seconds=timeout_seconds)
    if result.returncode != 0:
        return {}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, list):
        return {}
    agents: dict[str, dict[str, object]] = {}
    for item in parsed:
        if isinstance(item, dict) and item.get("id"):
            agents[str(item["id"])] = item
    return agents


def _seed_workspace(role: AgentRole) -> None:
    role.workspace.mkdir(parents=True, exist_ok=True)
    for file_name in get_list("agent.routing.workspace_seed_files"):
        source = OPENCLAW_WORKSPACE_DIR / str(file_name)
        target = role.workspace / str(file_name)
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)


def ensure_configured_agents(
    *,
    runner: OpenClawRunner,
    log,
    timeout_seconds: int | None = None,
) -> list[AgentEnsureResult]:
    ensure_runtime_dirs()
    timeout = timeout_seconds or get_int("openclaw.default_control_timeout_seconds")
    actual = _actual_agents(runner, timeout)
    results: list[AgentEnsureResult] = []

    for role in configured_roles():
        if not role.ensure_agent:
            results.append(
                AgentEnsureResult(
                    role=role.name,
                    agent=role.agent,
                    workspace=str(role.workspace),
                    existed=role.agent in actual,
                    created=False,
                    model_checked=False,
                    model_repaired=False,
                    returncode=0,
                    message="role uses existing default agent",
                )
            )
            continue

        _seed_workspace(role)
        existed = role.agent in actual
        created = False
        model_checked = False
        model_repaired = False
        returncode = 0
        message = "agent already exists"

        if not existed:
            result = runner.run(
                [
                    "agents",
                    "add",
                    role.agent,
                    "--workspace",
                    str(role.workspace),
                    "--model",
                    role.model,
                    "--non-interactive",
                    "--json",
                ],
                timeout_seconds=timeout,
            )
            created = result.returncode == 0
            returncode = result.returncode
            message = "agent created" if created else (result.stderr or result.stdout).strip()
            log(
                "agent role create attempted",
                level="OK" if created else "ERROR",
                role=role.name,
                agent=role.agent,
                returncode=result.returncode,
            )
        elif get_bool("agent.routing.repair_model_on_ensure"):
            status = runner.run(
                ["models", "--agent", role.agent, "status", "--plain"],
                timeout_seconds=timeout,
            )
            model_checked = status.returncode == 0
            current_model = status.stdout.strip()
            if status.returncode == 0 and current_model != role.model:
                repaired = runner.run(
                    ["models", "--agent", role.agent, "set", role.model],
                    timeout_seconds=timeout,
                )
                model_repaired = repaired.returncode == 0
                returncode = repaired.returncode
                message = "model repaired" if model_repaired else (repaired.stderr or repaired.stdout).strip()

        results.append(
            AgentEnsureResult(
                role=role.name,
                agent=role.agent,
                workspace=str(role.workspace),
                existed=existed,
                created=created,
                model_checked=model_checked,
                model_repaired=model_repaired,
                returncode=returncode,
                message=message,
            )
        )

    return results


def ensure_results_as_dicts(results: Iterable[AgentEnsureResult]) -> list[dict[str, object]]:
    return [asdict(result) for result in results]
