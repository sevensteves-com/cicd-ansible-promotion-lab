#!/usr/bin/env python3
"""Validate and resolve manifest-driven production Ansible components."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_PATH = ".github/prod-components.json"
MAX_SECRET_ENV = 8

COMPONENT_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SECRET_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
LIMIT_RE = re.compile(r"^[A-Za-z0-9_.!*,:&-]+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class ManifestError(ValueError):
    """Raised when the production component manifest is invalid."""


def run_git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ManifestError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{label} must be a non-empty string")
    return value


def validate_relative_path(value: Any, label: str, prefix: str) -> str:
    path = require_string(value, label)
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise ManifestError(f"{label} must be a safe relative path")
    if not path.startswith(prefix):
        raise ManifestError(f"{label} must start with {prefix!r}")
    return path


def validate_ansible_relative_path(value: Any, label: str) -> str:
    path = require_string(value, label)
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise ManifestError(f"{label} must be a safe path relative to ansible/")
    return path


def validate_component(component: Any, index: int) -> dict[str, Any]:
    label = f"components[{index}]"
    if not isinstance(component, dict):
        raise ManifestError(f"{label} must be an object")

    allowed_keys = {
        "id",
        "name",
        "inventory",
        "playbook",
        "limit",
        "env",
        "secret_env",
        "dependency_paths",
    }
    unknown_keys = sorted(set(component) - allowed_keys)
    if unknown_keys:
        raise ManifestError(f"{label} has unknown keys: {', '.join(unknown_keys)}")

    component_id = require_string(component.get("id"), f"{label}.id")
    if not COMPONENT_ID_RE.fullmatch(component_id):
        raise ManifestError(
            f"{label}.id must contain lowercase letters, digits, and internal hyphens"
        )

    name = require_string(component.get("name"), f"{label}.name")

    inventory = validate_relative_path(
        component.get("inventory"), f"{label}.inventory", "inventory/"
    )
    playbook = validate_relative_path(
        component.get("playbook"), f"{label}.playbook", "playbooks/"
    )

    limit = require_string(component.get("limit"), f"{label}.limit")
    if not LIMIT_RE.fullmatch(limit):
        raise ManifestError(f"{label}.limit contains unsupported characters")

    dependency_paths = component.get("dependency_paths", [])
    if not isinstance(dependency_paths, list):
        raise ManifestError(f"{label}.dependency_paths must be an array")
    validated_dependency_paths = []
    for path_index, path in enumerate(dependency_paths):
        validated_dependency_paths.append(
            validate_ansible_relative_path(
                path,
                f"{label}.dependency_paths[{path_index}]",
            )
        )
    if len(validated_dependency_paths) != len(set(validated_dependency_paths)):
        raise ManifestError(f"{label}.dependency_paths contains duplicates")

    environment = component.get("env", {})
    if not isinstance(environment, dict):
        raise ManifestError(f"{label}.env must be an object")
    validated_environment: dict[str, str] = {}
    for env_name, env_value in environment.items():
        if not isinstance(env_name, str) or not ENV_NAME_RE.fullmatch(env_name):
            raise ManifestError(f"{label}.env contains invalid name {env_name!r}")
        if env_name.startswith(("GITHUB_", "RUNNER_")):
            raise ManifestError(f"{label}.env may not override {env_name}")
        if not isinstance(env_value, str):
            raise ManifestError(f"{label}.env.{env_name} must be a string")
        validated_environment[env_name] = env_value

    secret_environment = component.get("secret_env", [])
    if not isinstance(secret_environment, list):
        raise ManifestError(f"{label}.secret_env must be an array")
    if len(secret_environment) > MAX_SECRET_ENV:
        raise ManifestError(
            f"{label}.secret_env supports at most {MAX_SECRET_ENV} entries"
        )

    validated_secret_environment: list[dict[str, str]] = []
    secret_env_names: set[str] = set()
    for secret_index, mapping in enumerate(secret_environment):
        secret_label = f"{label}.secret_env[{secret_index}]"
        if not isinstance(mapping, dict) or set(mapping) != {"env", "secret"}:
            raise ManifestError(
                f"{secret_label} must contain exactly 'env' and 'secret'"
            )
        env_name = require_string(mapping["env"], f"{secret_label}.env")
        secret_name = require_string(mapping["secret"], f"{secret_label}.secret")
        if not ENV_NAME_RE.fullmatch(env_name):
            raise ManifestError(f"{secret_label}.env is invalid")
        if env_name.startswith(("GITHUB_", "RUNNER_")):
            raise ManifestError(f"{secret_label}.env may not override {env_name}")
        if not SECRET_NAME_RE.fullmatch(secret_name):
            raise ManifestError(f"{secret_label}.secret is invalid")
        if env_name in validated_environment or env_name in secret_env_names:
            raise ManifestError(f"{secret_label}.env duplicates {env_name}")
        secret_env_names.add(env_name)
        validated_secret_environment.append(
            {"env": env_name, "secret": secret_name}
        )

    return {
        "id": component_id,
        "name": name,
        "inventory": inventory,
        "playbook": playbook,
        "limit": limit,
        "env": validated_environment,
        "secret_env": validated_secret_environment,
        "dependency_paths": validated_dependency_paths,
    }


def validate_manifest(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ManifestError("manifest must be an object")
    allowed_keys = {"scheduled_reconcile_enabled", "components"}
    unknown_keys = sorted(set(data) - allowed_keys)
    if unknown_keys:
        raise ManifestError(f"manifest has unknown keys: {', '.join(unknown_keys)}")

    schedule_enabled = data.get("scheduled_reconcile_enabled")
    if not isinstance(schedule_enabled, bool):
        raise ManifestError("scheduled_reconcile_enabled must be a boolean")

    components = data.get("components")
    if not isinstance(components, list) or not components:
        raise ManifestError("components must be a non-empty array")

    validated_components = [
        validate_component(component, index)
        for index, component in enumerate(components)
    ]
    component_ids = [component["id"] for component in validated_components]
    duplicate_ids = sorted(
        component_id
        for component_id in set(component_ids)
        if component_ids.count(component_id) > 1
    )
    if duplicate_ids:
        raise ManifestError(f"duplicate component ids: {', '.join(duplicate_ids)}")

    return {
        "scheduled_reconcile_enabled": schedule_enabled,
        "components": validated_components,
    }


def load_manifest_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read {path}: {error}") from error
    return validate_manifest(data)


def load_manifest_at_sha(sha: str) -> dict[str, Any]:
    raw_manifest = run_git("show", f"{sha}:{MANIFEST_PATH}")
    try:
        data = json.loads(raw_manifest)
    except json.JSONDecodeError as error:
        raise ManifestError(f"{MANIFEST_PATH} at {sha} is invalid JSON: {error}") from error
    return validate_manifest(data)


def ensure_component_files(component: dict[str, Any], sha: str | None = None) -> None:
    component_paths = [
        component["inventory"],
        component["playbook"],
        *component["dependency_paths"],
    ]
    for component_path in component_paths:
        relative_path = f"ansible/{component_path}"
        if sha is None:
            if not Path(relative_path).exists():
                raise ManifestError(
                    f"component {component['id']}: {relative_path} does not exist"
                )
        else:
            result = subprocess.run(
                ["git", "cat-file", "-e", f"{sha}:{relative_path}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                raise ManifestError(
                    f"component {component['id']}: {relative_path} is absent at {sha}"
                )


def find_component(manifest: dict[str, Any], component_id: str) -> dict[str, Any]:
    for component in manifest["components"]:
        if component["id"] == component_id:
            return component
    raise ManifestError(f"component {component_id!r} is not declared at the approved SHA")


def matrix_item(component: dict[str, Any], sha: str) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": component["id"],
        "name": component["name"],
        "inventory": component["inventory"],
        "playbook": component["playbook"],
        "limit": component["limit"],
        "env_json": json.dumps(component["env"], separators=(",", ":")),
        "sha": sha,
    }
    for slot in range(1, MAX_SECRET_ENV + 1):
        item[f"secret_{slot}_env"] = ""
        item[f"secret_{slot}_name"] = "PROD_COMPONENT_UNUSED"
    for slot, mapping in enumerate(component["secret_env"], start=1):
        item[f"secret_{slot}_env"] = mapping["env"]
        item[f"secret_{slot}_name"] = mapping["secret"]
    return item


def approval_matrix(manifest: dict[str, Any], sha: str) -> dict[str, Any]:
    sha = resolve_sha(sha)
    changed_components = []
    for component in manifest["components"]:
        ensure_component_files(component)
        applied_sha = latest_applied_sha(component["id"])
        baseline_sha, baseline_kind = latest_decision_baseline(component["id"])
        changed, reason = component_changed_since_apply(
            component,
            sha,
            baseline_sha,
        )
        if baseline_kind == "rejected":
            reason = (
                f"{reason} since rejection"
                if changed
                else "deployment inputs are unchanged since rejection"
            )
        state = "changed" if changed else "unchanged"
        baseline = (
            f"{baseline_kind} {baseline_sha[:7]}"
            if baseline_sha
            else "no prior decision"
        )
        print(
            f"{component['id']}: {state} versus {baseline} ({reason})",
            file=sys.stderr,
        )
        if changed:
            changed_components.append(
                {
                    "id": component["id"],
                    "name": component["name"],
                    "sha": sha,
                    "applied_sha": applied_sha or "",
                    "reason": reason,
                }
            )
    return {"include": changed_components}


def head_matrix(manifest: dict[str, Any], sha: str) -> dict[str, Any]:
    sha = resolve_sha(sha)
    for component in manifest["components"]:
        ensure_component_files(component)
    return {
        "include": [
            matrix_item(component, sha) for component in manifest["components"]
        ]
    }


def resolve_sha(value: str) -> str:
    sha = run_git("rev-parse", f"{value}^{{commit}}")
    if not SHA_RE.fullmatch(sha):
        raise ManifestError(f"{value!r} did not resolve to a full commit SHA")
    return sha.lower()


def verify_approval_tag(component_id: str, sha: str) -> None:
    prefix = f"prod-approved/{component_id}/"
    expected_suffix = sha[:7]
    refs = run_git(
        "for-each-ref",
        "--format=%(refname:short)|%(taggerdate:unix)",
        f"refs/tags/{prefix}*",
    )
    for line in refs.splitlines():
        tag, _, tagger_timestamp = line.partition("|")
        if not tagger_timestamp:
            continue
        tag_suffix = tag.removeprefix(prefix)
        legacy_tag = tag_suffix == expected_suffix
        event_tag = re.fullmatch(
            rf"[0-9]+-[0-9]+-{re.escape(expected_suffix)}",
            tag_suffix,
        )
        if (legacy_tag or event_tag) and resolve_sha(f"refs/tags/{tag}") == sha:
            return
    raise ManifestError(
        f"no annotated {prefix} tag approves requested SHA {sha}"
    )


def latest_approved_sha(component_id: str) -> str | None:
    return latest_component_tag_sha("prod-approved", component_id)


def latest_applied_sha(component_id: str) -> str | None:
    return latest_component_tag_sha("prod-applied", component_id)


def latest_rejected_sha(component_id: str) -> str | None:
    return latest_component_tag_sha("prod-rejected", component_id)


def latest_component_tag_sha(tag_prefix: str, component_id: str) -> str | None:
    marker = latest_component_tag_marker(tag_prefix, component_id)
    return marker[0] if marker else None


def latest_component_tag_marker(
    tag_prefix: str,
    component_id: str,
) -> tuple[str, int] | None:
    refs = run_git(
        "for-each-ref",
        "--sort=-taggerdate",
        "--format=%(refname:short)|%(taggerdate:unix)",
        f"refs/tags/{tag_prefix}/{component_id}/*",
    )
    for line in refs.splitlines():
        if not line:
            continue
        tag, _, tagger_timestamp = line.partition("|")
        # Component approval tags are annotated. Ignoring lightweight tags avoids
        # treating the commit date as an approval timestamp.
        if not tagger_timestamp:
            continue
        suffix = tag.rsplit("/", 1)[-1]
        event_match = re.fullmatch(r"([0-9]+)-[0-9]+-[0-9a-f]{7}", suffix)
        event_order = (
            int(event_match.group(1))
            if event_match
            else int(tagger_timestamp)
        )
        return resolve_sha(f"refs/tags/{tag}"), event_order
    return None


def latest_decision_baseline(component_id: str) -> tuple[str | None, str | None]:
    approved = latest_component_tag_marker("prod-approved", component_id)
    rejected = latest_component_tag_marker("prod-rejected", component_id)
    if rejected and (not approved or rejected[1] > approved[1]):
        return rejected[0], "rejected"
    if approved:
        return approved[0], "approved"
    applied = latest_component_tag_marker("prod-applied", component_id)
    if applied:
        return applied[0], "applied"
    return None, None


def deployment_configuration(component: dict[str, Any]) -> dict[str, Any]:
    return {
        key: component[key]
        for key in (
            "inventory",
            "playbook",
            "limit",
            "env",
            "secret_env",
            "dependency_paths",
        )
    }


def component_tracked_paths(component: dict[str, Any]) -> set[str]:
    return {
        f"ansible/{path}"
        for path in (
            component["inventory"],
            component["playbook"],
            *component["dependency_paths"],
        )
    }


def component_changed_since_apply(
    component: dict[str, Any],
    candidate_sha: str,
    applied_sha: str | None,
) -> tuple[bool, str]:
    if applied_sha is None:
        return True, "first production deployment"

    applied_manifest = load_manifest_at_sha(applied_sha)
    try:
        applied_component = find_component(applied_manifest, component["id"])
    except ManifestError:
        return True, "component was not declared at the applied SHA"

    if deployment_configuration(component) != deployment_configuration(
        applied_component
    ):
        return True, "component declaration changed"

    tracked_paths = sorted(
        component_tracked_paths(component)
        | component_tracked_paths(applied_component)
    )
    result = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            applied_sha,
            candidate_sha,
            "--",
            *tracked_paths,
        ],
        check=False,
    )
    if result.returncode == 0:
        return False, "deployment inputs are identical"
    if result.returncode == 1:
        return True, "tracked deployment content changed"
    raise ManifestError(
        f"git diff failed for component {component['id']} with "
        f"status {result.returncode}"
    )


def reconcile_matrix(
    event: str,
    manifest: dict[str, Any],
    component_id: str | None,
    requested_sha: str | None,
) -> dict[str, Any]:
    selections: list[tuple[dict[str, Any], str]] = []

    if event == "schedule":
        if not manifest["scheduled_reconcile_enabled"]:
            return {"include": []}
        for declared_component in manifest["components"]:
            latest_sha = latest_approved_sha(declared_component["id"])
            if latest_sha is None:
                continue
            approved_manifest = load_manifest_at_sha(latest_sha)
            approved_component = find_component(
                approved_manifest, declared_component["id"]
            )
            ensure_component_files(approved_component, latest_sha)
            selections.append((approved_component, latest_sha))
    elif event == "workflow_dispatch":
        if not component_id or not requested_sha:
            raise ManifestError(
                "workflow_dispatch requires both component and sha inputs"
            )
        if not COMPONENT_ID_RE.fullmatch(component_id):
            raise ManifestError("component input is invalid")
        sha = resolve_sha(requested_sha)
        verify_approval_tag(component_id, sha)
        approved_manifest = load_manifest_at_sha(sha)
        approved_component = find_component(approved_manifest, component_id)
        ensure_component_files(approved_component, sha)
        selections.append((approved_component, sha))
    else:
        raise ManifestError(f"unsupported event {event!r}")

    return {
        "include": [
            matrix_item(component, sha) for component, sha in selections
        ]
    }


def write_github_environment(name: str, value: str, output_path: Path) -> None:
    delimiter = f"PROD_COMPONENT_{os.urandom(16).hex()}"
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"{name}<<{delimiter}\n")
        output.write(value)
        if not value.endswith("\n"):
            output.write("\n")
        output.write(f"{delimiter}\n")


def export_environment() -> None:
    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        raise ManifestError("GITHUB_ENV is not set")
    output_path = Path(github_env)

    try:
        ordinary_environment = json.loads(os.environ.get("COMPONENT_ENV_JSON", "{}"))
    except json.JSONDecodeError as error:
        raise ManifestError(f"COMPONENT_ENV_JSON is invalid: {error}") from error
    if not isinstance(ordinary_environment, dict):
        raise ManifestError("COMPONENT_ENV_JSON must contain an object")

    exported_names: set[str] = set()
    for name, value in ordinary_environment.items():
        if not isinstance(name, str) or not ENV_NAME_RE.fullmatch(name):
            raise ManifestError(f"invalid component environment name {name!r}")
        if not isinstance(value, str):
            raise ManifestError(f"component environment value for {name} is not a string")
        write_github_environment(name, value, output_path)
        exported_names.add(name)

    for slot in range(1, MAX_SECRET_ENV + 1):
        name = os.environ.get(f"PROD_SECRET_{slot}_ENV", "")
        value = os.environ.get(f"PROD_SECRET_{slot}_VALUE", "")
        if not name:
            continue
        if not ENV_NAME_RE.fullmatch(name):
            raise ManifestError(f"invalid secret environment name {name!r}")
        if name in exported_names:
            raise ManifestError(f"duplicate exported environment name {name}")
        if not value:
            raise ManifestError(f"required GitHub Actions secret for {name} is empty")
        write_github_environment(name, value, output_path)
        exported_names.add(name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path, default=Path(MANIFEST_PATH), help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate")

    approval_parser = subparsers.add_parser("approval-matrix")
    approval_parser.add_argument("--sha", required=True)

    head_parser = subparsers.add_parser("head-matrix")
    head_parser.add_argument("--sha", required=True)

    reconcile_parser = subparsers.add_parser("reconcile-matrix")
    reconcile_parser.add_argument(
        "--event", choices=("schedule", "workflow_dispatch"), required=True
    )
    reconcile_parser.add_argument("--component")
    reconcile_parser.add_argument("--sha")

    subparsers.add_parser("export-env")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "export-env":
            export_environment()
            return 0

        manifest = load_manifest_file(args.manifest)
        if args.command == "validate":
            for component in manifest["components"]:
                ensure_component_files(component)
            print(
                f"Validated {len(manifest['components'])} production components",
                file=sys.stderr,
            )
            return 0
        if args.command == "approval-matrix":
            print(
                json.dumps(
                    approval_matrix(manifest, args.sha), separators=(",", ":")
                )
            )
            return 0
        if args.command == "head-matrix":
            print(
                json.dumps(head_matrix(manifest, args.sha), separators=(",", ":"))
            )
            return 0
        if args.command == "reconcile-matrix":
            print(
                json.dumps(
                    reconcile_matrix(
                        args.event, manifest, args.component, args.sha
                    ),
                    separators=(",", ":"),
                )
            )
            return 0
        raise AssertionError(f"unhandled command {args.command}")
    except ManifestError as error:
        print(f"prod-components: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
