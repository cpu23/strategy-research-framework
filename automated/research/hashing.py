from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .schemas import REPO_ROOT, load_yaml, validate_strategy_spec


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [canonicalize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def stable_hash(value: Any) -> str:
    payload = json.dumps(canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bound_packet_digest(approval: dict[str, Any], decision_packet_path: str | Path) -> str | None:
    """Check if the current decision packet digest matches the bound digest.

    Returns None if digests match or no stored digest exists.
    Returns an error message string on mismatch.
    """
    try:
        meta = json.loads(approval.get("scope_metadata_json") or "{}")
    except Exception:
        return None
    stored_digest = meta.get("decision_packet_digest", "")
    if not stored_digest:
        return None
    current_digest = file_sha256(decision_packet_path)
    if current_digest != stored_digest:
        return (
            f"Decision packet digest mismatch. Approval was bound to digest {stored_digest}, "
            f"but current packet has digest {current_digest}. Packet may have been edited."
        )
    return None


def hash_strategy_spec(path_or_data: str | Path | dict[str, Any]) -> str:
    if isinstance(path_or_data, (str, Path)):
        data = load_yaml(path_or_data)
    else:
        data = path_or_data
    validate_strategy_spec(data, require_files=False)
    return stable_hash(data)


def parse_key_value_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";") or line.startswith("//"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def hash_parameter_set(path: str | Path) -> str:
    return stable_hash({"parameter_set": parse_key_value_file(path)})


def hash_execution_config(path: str | Path) -> str:
    return stable_hash({"execution_config": parse_key_value_file(path)})


def hash_cost_config(cost_config: dict[str, Any]) -> str:
    return stable_hash({"costs": cost_config})


def git_code_version(root: str | Path = REPO_ROOT) -> str:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unavailable:not_a_git_repository"
    try:
        dirty = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        dirty = ""
    return f"{commit}{'-dirty' if dirty else ''}"
