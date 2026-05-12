"""Model availability diagnostics for configured assistant routes."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable

from .config import get_bool, get_int, get_list, get_str, get_table, get_value


@dataclass(frozen=True)
class ModelCheck:
    key: str
    purpose: str
    model: str
    required: bool
    status: str
    note: str


def parse_model_list(output: str) -> list[str]:
    """Parse `openclaw models list --plain` output into unique model ids."""

    ansi_regex = re.compile(get_str("models.validation.ansi_escape_regex"))
    models: list[str] = []
    for raw_line in output.splitlines():
        line = ansi_regex.sub("", raw_line).strip()
        if not line:
            continue
        if "/" not in line:
            continue
        models.append(line)
    return sorted(set(models))


def _target_values(key: str) -> list[str]:
    value = get_value(key)
    if isinstance(value, list):
        return [str(item).strip() for item in value]
    return [str(value).strip()]


def _status_for_model(model: str, provider: str, visible_models: set[str]) -> tuple[str, str]:
    if not model:
        return get_str("models.validation.empty_status"), get_str("models.validation.empty_note")
    if model in visible_models:
        return get_str("models.validation.available_status"), get_str("models.validation.available_note")

    provider_prefixed = f"{provider}/{model}"
    if "/" not in model and provider_prefixed in visible_models:
        return (
            get_str("models.validation.provider_prefixed_status"),
            get_str("models.validation.provider_prefixed_note"),
        )

    return get_str("models.validation.missing_status"), get_str("models.validation.missing_note")


def validate_configured_models(*, provider: str, visible_models: Iterable[str]) -> list[ModelCheck]:
    visible = set(visible_models)
    checks: list[ModelCheck] = []
    for target in get_table("models.validation").get("targets", []):
        if not isinstance(target, dict):
            continue
        key = str(target.get("key", "")).strip()
        if not key:
            continue
        purpose = str(target.get("purpose", "")).strip()
        required = bool(target.get("required", False))
        for model in _target_values(key):
            status, note = _status_for_model(model, provider, visible)
            checks.append(
                ModelCheck(
                    key=key,
                    purpose=purpose,
                    model=model,
                    required=required,
                    status=status,
                    note=note,
                )
            )
    return checks


def has_required_missing(checks: Iterable[ModelCheck]) -> bool:
    missing_status = get_str("models.validation.missing_status")
    empty_status = get_str("models.validation.empty_status")
    return any(check.required and check.status in {missing_status, empty_status} for check in checks)


def returncode_for_checks(checks: Iterable[ModelCheck]) -> int:
    if get_bool("models.validation.missing_is_failure") and has_required_missing(checks):
        return get_int("models.validation.missing_returncode")
    return 0


def checks_as_dicts(checks: Iterable[ModelCheck]) -> list[dict[str, object]]:
    return [asdict(check) for check in checks]


def format_model_checks(*, provider: str, visible_models: list[str], checks: Iterable[ModelCheck]) -> str:
    lines = [
        "Model validation",
        f"Provider: {provider}",
        f"Visible models: {len(visible_models)}",
        "",
    ]
    for check in checks:
        required = "required" if check.required else "desired"
        lines.append(f"- {check.key} | {required} | {check.status}")
        lines.append(f"  model: {check.model}")
        if check.purpose:
            lines.append(f"  purpose: {check.purpose}")
        lines.append(f"  note: {check.note}")
    return "\n".join(lines)
