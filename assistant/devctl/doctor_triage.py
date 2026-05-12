"""OpenClaw doctor report triage.

The triage layer is intentionally read-only. It classifies known doctor output
using config-driven patterns and recommendations, but never applies fixes.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .config import get_int, get_list, get_str
from .paths import OPENCLAW_RUNS_DIR


@dataclass(frozen=True)
class DoctorIssue:
    id: str
    severity: str
    safe_to_fix_automatically: bool
    recommendation: str
    matched_line: str


def latest_doctor_artifact() -> Path | None:
    artifacts = sorted(
        OPENCLAW_RUNS_DIR.glob(get_str("doctor.artifact_glob")),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    return artifacts[0] if artifacts else None


def text_from_artifact(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result", {})
    if not isinstance(result, dict):
        return ""
    chunks = [str(result.get("stdout", "")), str(result.get("stderr", ""))]
    return "\n".join(chunk for chunk in chunks if chunk)


def text_from_result(stdout: str, stderr: str) -> str:
    return "\n".join(chunk for chunk in [stdout, stderr] if chunk)


def _first_match_line(text: str, pattern: re.Pattern[str]) -> str:
    limit = get_int("doctor.matched_line_max_chars")
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()[:limit]
    match = pattern.search(text)
    if not match:
        return ""
    return match.group(0).strip()[:limit]


def triage_text(text: str) -> list[DoctorIssue]:
    issues: list[DoctorIssue] = []
    for item in get_list("doctor.issue_patterns"):
        if not isinstance(item, dict):
            continue
        pattern_text = str(item.get("pattern", "")).strip()
        if not pattern_text:
            continue
        pattern = re.compile(pattern_text, re.IGNORECASE | re.MULTILINE)
        if not pattern.search(text):
            continue
        issues.append(
            DoctorIssue(
                id=str(item.get("id", "")).strip(),
                severity=str(item.get("severity", "")).strip(),
                safe_to_fix_automatically=bool(item.get("safe_to_fix_automatically", False)),
                recommendation=str(item.get("recommendation", "")).strip(),
                matched_line=_first_match_line(text, pattern),
            )
        )
    return issues


def issues_as_dicts(issues: Iterable[DoctorIssue]) -> list[dict[str, object]]:
    return [asdict(issue) for issue in issues]


def format_issues(*, source: str, issues: Iterable[DoctorIssue]) -> str:
    issue_list = list(issues)
    lines = [
        "OpenClaw doctor triage",
        f"Source: {source}",
        "",
    ]
    if not issue_list:
        lines.append(get_str("doctor.no_issues_message"))
        return "\n".join(lines)

    for issue in issue_list:
        safe_fix = "yes" if issue.safe_to_fix_automatically else "no"
        lines.append(f"- {issue.id} | {issue.severity} | auto-fix-safe={safe_fix}")
        if issue.matched_line:
            lines.append(f"  match: {issue.matched_line}")
        if issue.recommendation:
            lines.append(f"  next: {issue.recommendation}")
    return "\n".join(lines)


def triage_payload(*, source: str, issues: Iterable[DoctorIssue]) -> dict[str, object]:
    issue_list = list(issues)
    return {
        "source": source,
        "issues": issues_as_dicts(issue_list),
        "issue_count": len(issue_list),
    }
