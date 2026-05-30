from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import implementation as impl_mod
from .hashing import file_sha256
from .schemas import SchemaValidationError


@dataclass(frozen=True)
class RealCompileConfig:
    mode: str = "real_compile"
    wine_binary: str = "wine"
    wine_prefix: str | None = None
    metaeditor_path: str | None = None
    terminal_data_dir: str | None = None
    timeout_seconds: int = 120


def load_real_compile_config(path: str | Path) -> RealCompileConfig:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Real compile config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Real compile config must be a YAML mapping")
    mode = raw.get("mode", "real_compile")
    if mode != "real_compile":
        raise ValueError(f"Expected mode 'real_compile', got {mode!r}")
    validate_real_compile_config(raw)
    return RealCompileConfig(
        mode=mode,
        wine_binary=str(raw.get("wine_binary", "wine")),
        wine_prefix=str(raw["wine_prefix"]) if raw.get("wine_prefix") else None,
        metaeditor_path=str(raw["metaeditor_path"]) if raw.get("metaeditor_path") else None,
        terminal_data_dir=str(raw["terminal_data_dir"]) if raw.get("terminal_data_dir") else None,
        timeout_seconds=int(raw.get("timeout_seconds", 120)),
    )


def validate_real_compile_config(raw: dict[str, Any]) -> None:
    errors: list[str] = []
    if not isinstance(raw.get("mode"), str):
        errors.append("mode must be a string")
    val = raw.get("wine_prefix")
    if val is not None and not isinstance(val, str):
        errors.append("wine_prefix must be a string or null")
    val = raw.get("metaeditor_path")
    if val is not None and not isinstance(val, str):
        errors.append("metaeditor_path must be a string or null")
    val = raw.get("terminal_data_dir")
    if val is not None and not isinstance(val, str):
        errors.append("terminal_data_dir must be a string or null")
    if "timeout_seconds" in raw and (not isinstance(raw["timeout_seconds"], int) or raw["timeout_seconds"] < 1):
        errors.append("timeout_seconds must be a positive integer")
    if errors:
        raise ValueError("; ".join(errors))


def build_compile_command(mq5_path: Path, config: RealCompileConfig) -> list[str]:
    cmd = [config.wine_binary]
    if config.wine_prefix:
        cmd.extend(["WINEPREFIX", config.wine_prefix])
    if config.metaeditor_path:
        cmd.append(config.metaeditor_path)
    else:
        cmd.append("metaeditor64.exe")
    cmd.extend(["/compile", str(mq5_path.resolve())])
    if config.terminal_data_dir:
        cmd.extend(["/log", config.terminal_data_dir])
    return cmd


def _compute_compile_input_digests(
    mq5_path: Path,
    config_path: str | Path | None = None,
) -> dict[str, str]:
    digests: dict[str, str] = {}
    resolved = mq5_path.resolve()
    if resolved.is_file():
        digests["generated_mq5"] = file_sha256(resolved)
    if config_path:
        cp = Path(config_path).resolve()
        if cp.is_file():
            digests["compile_config"] = file_sha256(cp)
    return digests


def run_real_compile(
    mq5_path: Path,
    config: RealCompileConfig,
    *,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    resolved = mq5_path.resolve()
    input_digests = _compute_compile_input_digests(resolved, config_path=config_path)
    now_stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _result(**overrides: Any) -> dict[str, Any]:
        base = {
            "mode": "real_compile",
            "input_digests": input_digests,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "compiler_log_path": None,
            "started_at": now_stamp,
            "finished_at": now_stamp,
            "duration_seconds": 0.0,
            "errors": [],
        }
        base.update(overrides)
        return base

    try:
        impl_mod.assert_sandbox_path(resolved)
    except SchemaValidationError as exc:
        return _result(status="failed", errors=[str(exc)])

    forbidden = impl_mod.check_no_production_touch([resolved])
    if forbidden:
        return _result(status="failed", errors=forbidden)

    if not resolved.is_file():
        return _result(status="failed", errors=[f"Target .mq5 file not found: {resolved}"])

    wine_binary = shutil.which(config.wine_binary)
    if not wine_binary and config.wine_binary != "wine":
        return _result(status="failed", errors=[f"Compiler binary not found: {config.wine_binary}"])

    cmd = build_compile_command(resolved, config)
    started_at = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired:
        finished_at = datetime.now(timezone.utc)
        duration = (finished_at - started_at).total_seconds()
        return _result(
            status="failed",
            errors=[f"Compile timed out after {config.timeout_seconds}s"],
            started_at=started_at.isoformat(timespec="seconds"),
            finished_at=finished_at.isoformat(timespec="seconds"),
            duration_seconds=duration,
        )
    except FileNotFoundError:
        finished_at = datetime.now(timezone.utc)
        duration = (finished_at - started_at).total_seconds()
        return _result(
            status="failed",
            errors=[f"Compiler executable not found: {cmd[0]}"],
            started_at=started_at.isoformat(timespec="seconds"),
            finished_at=finished_at.isoformat(timespec="seconds"),
            duration_seconds=duration,
        )
    else:
        finished_at = datetime.now(timezone.utc)
        duration = (finished_at - started_at).total_seconds()
        status = "passed" if exit_code == 0 else "failed"
        errors: list[str] = []
        if status == "failed":
            errors.append(f"Compiler exited with code {exit_code}")
        return _result(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at.isoformat(timespec="seconds"),
            finished_at=finished_at.isoformat(timespec="seconds"),
            duration_seconds=duration,
            errors=errors,
        )
