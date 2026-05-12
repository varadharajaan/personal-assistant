"""Local/S3 archive helpers for assistant memory and context."""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import get_bool, get_int, get_list, get_path, get_str, project_root


@dataclass(frozen=True)
class ArchiveFile:
    path: str
    arcname: str
    size: int


@dataclass(frozen=True)
class ArchivePlan:
    files: list[ArchiveFile]
    total_bytes: int
    destination: str
    compression_backend: str
    compression_profile: str
    s3_enabled: bool
    s3_uri: str


@dataclass(frozen=True)
class AwsResult:
    returncode: int
    stdout: str
    stderr: str


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    normalized = value.replace("\\", "/")
    name = Path(value).name
    return any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _is_excluded(path: Path) -> bool:
    names = {str(item) for item in get_list("archive.exclude_names")}
    patterns = [str(item) for item in get_list("archive.exclude_patterns")]
    return any(part in names for part in path.parts) or _matches_any(str(path), patterns)


def _is_safe_file(path: Path) -> bool:
    suffixes = {str(item).lower() for item in get_list("archive.safe_suffixes")}
    if path.suffix.lower() not in suffixes:
        return False
    if _is_excluded(path):
        return False
    try:
        return path.stat().st_size <= get_int("archive.max_file_bytes")
    except OSError:
        return False


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def _archive_destination() -> Path:
    timestamp = datetime.now().strftime(get_str("archive.timestamp_format"))
    name = get_str("archive.archive_name_template").format(timestamp=timestamp)
    return get_path("archive.local_archive_dir") / name


def _aws_cli() -> str:
    for candidate in get_list("archive.aws_cli_candidates"):
        resolved = _resolve_executable(str(candidate))
        if resolved:
            return resolved
    return ""


def _resolve_executable(candidate: str) -> str:
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve())
    resolved = shutil.which(candidate)
    return resolved or ""


def _seven_zip_cli() -> str:
    for candidate in get_list("archive.seven_zip_executable_candidates"):
        resolved = _resolve_executable(str(candidate))
        if resolved:
            return resolved
    return ""


def _aws_s3api(args: list[str]) -> AwsResult:
    aws = _aws_cli()
    if not aws:
        return AwsResult(returncode=2, stdout="", stderr="AWS CLI was not found.")

    command = [aws, "s3api", *args]
    if get_str("archive.aws_profile"):
        command.extend(["--profile", get_str("archive.aws_profile")])
    if get_str("archive.s3_region"):
        command.extend(["--region", get_str("archive.s3_region")])

    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return AwsResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _compact_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _print_aws_result(result: AwsResult) -> None:
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())


def build_archive_plan() -> ArchivePlan:
    repo_root = project_root()
    destination = _archive_destination()
    files: dict[str, ArchiveFile] = {}

    for raw_path in get_list("archive.include_paths"):
        include_root = Path(str(raw_path)).expanduser().resolve()
        for path in _iter_files(include_root):
            if not _is_safe_file(path):
                continue
            try:
                arcname = path.relative_to(repo_root).as_posix()
            except ValueError:
                arcname = path.name
            files[str(path)] = ArchiveFile(path=str(path), arcname=arcname, size=path.stat().st_size)

    ordered = sorted(files.values(), key=lambda item: item.arcname)
    s3_uri = ""
    if get_bool("archive.s3_enabled") and get_str("archive.s3_bucket"):
        prefix = get_str("archive.s3_prefix").strip("/")
        s3_uri = f"s3://{get_str('archive.s3_bucket').strip('/')}/{prefix}/{destination.name}"
    return ArchivePlan(
        files=ordered,
        total_bytes=sum(item.size for item in ordered),
        destination=str(destination),
        compression_backend=get_str("archive.compression_backend"),
        compression_profile=get_str("archive.compression_profile"),
        s3_enabled=get_bool("archive.s3_enabled"),
        s3_uri=s3_uri,
    )


def archive_plan_as_dict(plan: ArchivePlan) -> dict[str, object]:
    return {
        "destination": plan.destination,
        "compression_backend": plan.compression_backend,
        "file_count": len(plan.files),
        "total_bytes": plan.total_bytes,
        "s3_enabled": plan.s3_enabled,
        "s3_uri": plan.s3_uri,
        "compression_profile": plan.compression_profile,
        "files": [asdict(item) for item in plan.files],
    }


def build_s3_lifecycle_configuration() -> dict[str, object]:
    rules: list[dict[str, object]] = []
    if get_bool("archive.s3_lifecycle_enabled"):
        rules.append(
            {
                "ID": get_str("archive.s3_lifecycle_rule_id"),
                "Filter": {"Prefix": get_str("archive.s3_lifecycle_filter_prefix")},
                "Status": "Enabled",
                "Expiration": {"Days": get_int("archive.s3_lifecycle_expiration_days")},
                "NoncurrentVersionExpiration": {
                    "NoncurrentDays": get_int("archive.s3_lifecycle_noncurrent_expiration_days")
                },
            }
        )

    rules.append(
        {
            "ID": get_str("archive.s3_lifecycle_abort_rule_id"),
            "Filter": {"Prefix": ""},
            "Status": "Enabled",
            "AbortIncompleteMultipartUpload": {
                "DaysAfterInitiation": get_int("archive.s3_lifecycle_abort_multipart_days")
            },
        }
    )
    return {"Rules": rules}


def build_s3_object_lock_configuration() -> dict[str, object]:
    return {
        "ObjectLockEnabled": "Enabled",
        "Rule": {
            "DefaultRetention": {
                "Mode": get_str("archive.s3_object_lock_mode"),
                "Days": get_int("archive.s3_object_lock_days"),
            }
        },
    }


def s3_archive_policy_plan_as_dict() -> dict[str, object]:
    return {
        "bucket": get_str("archive.s3_bucket"),
        "region": get_str("archive.s3_region"),
        "aws_profile": get_str("archive.aws_profile"),
        "delete_protection_policy_enabled": get_bool("archive.s3_delete_protection_policy_enabled"),
        "lifecycle_mutation_deny_sid": get_str("archive.s3_lifecycle_mutation_deny_sid"),
        "lifecycle": build_s3_lifecycle_configuration(),
        "object_lock": build_s3_object_lock_configuration()
        if get_bool("archive.s3_object_lock_enabled")
        else None,
    }


def format_s3_archive_policy_plan() -> str:
    return json.dumps(s3_archive_policy_plan_as_dict(), indent=get_int("archive.json_indent"))


def _bucket_policy() -> tuple[AwsResult, dict[str, object] | None]:
    result = _aws_s3api(["get-bucket-policy", "--bucket", get_str("archive.s3_bucket")])
    if result.returncode != 0:
        return result, None
    payload = json.loads(result.stdout)
    return result, json.loads(str(payload["Policy"]))


def _put_bucket_policy(policy: dict[str, object]) -> AwsResult:
    return _aws_s3api(
        [
            "put-bucket-policy",
            "--bucket",
            get_str("archive.s3_bucket"),
            "--policy",
            _compact_json(policy),
        ]
    )


def _policy_without_statement(policy: dict[str, object], sid: str) -> dict[str, object]:
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    filtered = [item for item in statements if not isinstance(item, dict) or item.get("Sid") != sid]
    updated = dict(policy)
    updated["Statement"] = filtered
    return updated


def _policy_has_statement(policy: dict[str, object], sid: str) -> bool:
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    return any(isinstance(item, dict) and item.get("Sid") == sid for item in statements)


def apply_s3_archive_policy(*, log, confirm: bool) -> int:
    if not get_bool("archive.s3_enabled"):
        print("S3 archive is disabled in config.")
        return 2
    if get_bool("archive.s3_lifecycle_apply_requires_confirm") and not confirm:
        print("S3 archive lifecycle/object-lock apply requires --confirm.")
        return 2

    original_policy: dict[str, object] | None = None
    restored = True
    sid = get_str("archive.s3_lifecycle_mutation_deny_sid")

    if get_bool("archive.s3_delete_protection_policy_enabled") and sid:
        policy_result, policy = _bucket_policy()
        if policy_result.returncode != 0 or policy is None:
            _print_aws_result(policy_result)
            log("s3 bucket policy read failed", level="ERROR", returncode=policy_result.returncode)
            return policy_result.returncode

        original_policy = policy
        relaxed_policy = _policy_without_statement(policy, sid)
        if relaxed_policy != policy:
            relaxed_result = _put_bucket_policy(relaxed_policy)
            if relaxed_result.returncode != 0:
                _print_aws_result(relaxed_result)
                log("s3 lifecycle mutation guard relax failed", level="ERROR", returncode=relaxed_result.returncode)
                return relaxed_result.returncode
            restored = False
            log("s3 lifecycle mutation guard temporarily relaxed", level="WARN", sid=sid)
            wait_seconds = get_int("archive.s3_policy_propagation_wait_seconds")
            if wait_seconds > 0:
                time.sleep(wait_seconds)

    lifecycle_result = AwsResult(returncode=0, stdout="", stderr="")
    object_lock_result = AwsResult(returncode=0, stdout="", stderr="")
    try:
        lifecycle_result = _aws_s3api(
            [
                "put-bucket-lifecycle-configuration",
                "--bucket",
                get_str("archive.s3_bucket"),
                "--lifecycle-configuration",
                _compact_json(build_s3_lifecycle_configuration()),
            ]
        )
        _print_aws_result(lifecycle_result)
        log(
            "s3 lifecycle apply finished",
            level="OK" if lifecycle_result.returncode == 0 else "ERROR",
            returncode=lifecycle_result.returncode,
            expiration_days=get_int("archive.s3_lifecycle_expiration_days"),
            noncurrent_days=get_int("archive.s3_lifecycle_noncurrent_expiration_days"),
        )

        if lifecycle_result.returncode == 0 and get_bool("archive.s3_object_lock_enabled"):
            object_lock_result = _aws_s3api(
                [
                    "put-object-lock-configuration",
                    "--bucket",
                    get_str("archive.s3_bucket"),
                    "--object-lock-configuration",
                    _compact_json(build_s3_object_lock_configuration()),
                ]
            )
            _print_aws_result(object_lock_result)
            log(
                "s3 object lock default apply finished",
                level="OK" if object_lock_result.returncode == 0 else "ERROR",
                returncode=object_lock_result.returncode,
                mode=get_str("archive.s3_object_lock_mode"),
                days=get_int("archive.s3_object_lock_days"),
            )
    finally:
        if original_policy is not None and not restored:
            restore_result = _put_bucket_policy(original_policy)
            if restore_result.returncode != 0:
                _print_aws_result(restore_result)
                log("s3 lifecycle mutation guard restore failed", level="ERROR", returncode=restore_result.returncode)
                return restore_result.returncode
            log("s3 lifecycle mutation guard restored", level="OK", sid=sid)

    return lifecycle_result.returncode or object_lock_result.returncode


def s3_archive_policy_status_as_dict(*, log) -> dict[str, object]:
    lifecycle_result = _aws_s3api(
        ["get-bucket-lifecycle-configuration", "--bucket", get_str("archive.s3_bucket")]
    )
    object_lock_result = _aws_s3api(
        ["get-object-lock-configuration", "--bucket", get_str("archive.s3_bucket")]
    )
    policy_result, policy = _bucket_policy()

    lifecycle = json.loads(lifecycle_result.stdout) if lifecycle_result.returncode == 0 else None
    object_lock = json.loads(object_lock_result.stdout) if object_lock_result.returncode == 0 else None
    object_lock_config = _nested(object_lock, "ObjectLockConfiguration") if object_lock else None
    lifecycle_rule = _find_rule(lifecycle, get_str("archive.s3_lifecycle_rule_id")) if lifecycle else None
    abort_rule = _find_rule(lifecycle, get_str("archive.s3_lifecycle_abort_rule_id")) if lifecycle else None

    checks = {
        "lifecycle_read_ok": lifecycle_result.returncode == 0,
        "object_lock_read_ok": object_lock_result.returncode == 0,
        "bucket_policy_read_ok": policy_result.returncode == 0,
        "expiration_days": _nested(lifecycle_rule, "Expiration", "Days")
        == get_int("archive.s3_lifecycle_expiration_days"),
        "noncurrent_days": _nested(lifecycle_rule, "NoncurrentVersionExpiration", "NoncurrentDays")
        == get_int("archive.s3_lifecycle_noncurrent_expiration_days"),
        "abort_multipart_days": _nested(abort_rule, "AbortIncompleteMultipartUpload", "DaysAfterInitiation")
        == get_int("archive.s3_lifecycle_abort_multipart_days"),
        "object_lock_days": _nested(object_lock_config, "Rule", "DefaultRetention", "Days")
        == get_int("archive.s3_object_lock_days"),
        "lifecycle_mutation_denied_by_policy": _policy_has_statement(
            policy or {}, get_str("archive.s3_lifecycle_mutation_deny_sid")
        ),
    }
    log("s3 archive policy status requested", level="OK" if all(checks.values()) else "WARN", **checks)
    return {
        "bucket": get_str("archive.s3_bucket"),
        "checks": checks,
        "lifecycle": lifecycle,
        "object_lock": object_lock,
    }


def _find_rule(lifecycle: dict[str, object] | None, rule_id: str) -> dict[str, object] | None:
    if not lifecycle:
        return None
    for rule in lifecycle.get("Rules", []):
        if isinstance(rule, dict) and rule.get("ID") == rule_id:
            return rule
    return None


def _nested(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def create_archive(*, log) -> ArchivePlan:
    plan = build_archive_plan()
    destination = Path(plan.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    if plan.compression_backend == "7z":
        seven_zip = _seven_zip_cli()
        if not seven_zip:
            print("7-Zip executable was not found.")
            raise RuntimeError("7-Zip executable was not found.")
        staging_root = get_path("archive.staging_root_dir")
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=get_str("archive.staging_prefix"), dir=staging_root) as temp_dir:
            staging_dir = Path(temp_dir)
            for item in plan.files:
                staged_path = staging_dir / item.arcname
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item.path, staged_path)

            command = [
                seven_zip,
                *[str(item) for item in get_list("archive.seven_zip_arguments")],
                str(destination),
                ".",
            ]
            result = subprocess.run(
                command,
                cwd=str(staging_dir),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.stdout:
                print(result.stdout.rstrip())
            if result.stderr:
                print(result.stderr.rstrip())
            if result.returncode != 0:
                log("archive compression failed", level="ERROR", backend=plan.compression_backend, returncode=result.returncode)
                raise RuntimeError(f"Archive compression failed with returncode {result.returncode}")
    else:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in plan.files:
                archive.write(item.path, item.arcname)

    archive_bytes = destination.stat().st_size if destination.exists() else 0
    log(
        "archive created",
        level="OK",
        path=str(destination),
        file_count=len(plan.files),
        source_bytes=plan.total_bytes,
        archive_bytes=archive_bytes,
        compression_backend=plan.compression_backend,
        compression_profile=plan.compression_profile,
    )
    return plan


def upload_archive_to_s3(plan: ArchivePlan, *, log, confirm: bool) -> int:
    if not plan.s3_enabled:
        print("S3 archive is disabled in config.")
        return 2
    if get_bool("archive.s3_requires_confirm") and not confirm:
        print("S3 upload requires --confirm.")
        return 2
    if not plan.s3_uri:
        print("S3 bucket/prefix is not configured.")
        return 2

    aws = _aws_cli()
    if not aws:
        print("AWS CLI was not found.")
        return 2

    command = [aws, "s3", "cp", plan.destination, plan.s3_uri]
    if get_str("archive.aws_profile"):
        command.extend(["--profile", get_str("archive.aws_profile")])
    if get_str("archive.s3_region"):
        command.extend(["--region", get_str("archive.s3_region")])
    log("s3 archive upload started", bucket=get_str("archive.s3_bucket"), prefix=get_str("archive.s3_prefix"))
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    log("s3 archive upload finished", level="OK" if result.returncode == 0 else "ERROR", returncode=result.returncode)
    return result.returncode


def format_archive_plan(plan: ArchivePlan) -> str:
    payload = archive_plan_as_dict(plan)
    return json.dumps(payload, indent=get_int("archive.json_indent"))
