from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import implementation as impl_mod, registry
from .hashing import file_sha256
from .schemas import REPO_ROOT, SANDBOX_ROOT, SchemaValidationError

MAX_TIMEOUT_SECONDS = 600
ALLOWED_BROKER_VALUES = {"mock"}


@dataclass(frozen=True)
class RealBacktestReadinessConfig:
    mode: str = "real_backtest_readiness"
    wine_binary: str = "wine"
    wine_prefix: str | None = None
    terminal_path: str | None = None
    terminal_data_dir: str | None = None
    timeout_seconds: int = 180
    max_duration_seconds: int = 180
    expected_symbol: str = "XAUUSD"
    expected_timeframe: str = "H4"
    expected_dataset_id: str | None = None
    runner_conf_path: str = ""
    set_file_path: str = ""
    output_dir: str = ""


def load_real_backtest_readiness_config(path: str | Path) -> RealBacktestReadinessConfig:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Real backtest readiness config not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Real backtest readiness config must be a YAML mapping")
    mode = raw.get("mode", "real_backtest_readiness")
    if mode != "real_backtest_readiness":
        raise ValueError(f"Expected mode 'real_backtest_readiness', got {mode!r}")
    validate_real_backtest_readiness_config(raw)
    return RealBacktestReadinessConfig(
        mode=mode,
        wine_binary=str(raw.get("wine_binary", "wine")),
        wine_prefix=str(raw["wine_prefix"]) if raw.get("wine_prefix") else None,
        terminal_path=str(raw["terminal_path"]) if raw.get("terminal_path") else None,
        terminal_data_dir=str(raw["terminal_data_dir"]) if raw.get("terminal_data_dir") else None,
        timeout_seconds=int(raw.get("timeout_seconds", 180)),
        max_duration_seconds=int(raw.get("max_duration_seconds", 180)),
        expected_symbol=str(raw.get("expected_symbol", "XAUUSD")),
        expected_timeframe=str(raw.get("expected_timeframe", "H4")),
        expected_dataset_id=str(raw["expected_dataset_id"]) if raw.get("expected_dataset_id") else None,
        runner_conf_path=str(raw.get("runner_conf_path", "")),
        set_file_path=str(raw.get("set_file_path", "")),
        output_dir=str(raw.get("output_dir", "")),
    )


def validate_real_backtest_readiness_config(raw: dict[str, Any]) -> None:
    errors: list[str] = []
    if not isinstance(raw.get("mode"), str):
        errors.append("mode must be a string")
    for field in ("wine_binary",):
        val = raw.get(field)
        if val is not None and not isinstance(val, str):
            errors.append(f"{field} must be a string")
    for field in ("wine_prefix", "terminal_path", "terminal_data_dir"):
        val = raw.get(field)
        if val is not None and not isinstance(val, str):
            errors.append(f"{field} must be a string or null")
    for field in ("expected_symbol", "expected_timeframe", "runner_conf_path", "set_file_path", "output_dir"):
        val = raw.get(field)
        if val is None or not isinstance(val, str) or not val.strip():
            errors.append(f"{field} is required and must be a non-empty string")
    for field in ("timeout_seconds", "max_duration_seconds"):
        if field in raw:
            val = raw[field]
            if not isinstance(val, int) or val < 1 or val > MAX_TIMEOUT_SECONDS:
                errors.append(f"{field} must be a positive integer <= {MAX_TIMEOUT_SECONDS}")
    if errors:
        raise ValueError("; ".join(errors))


def _parse_conf_value(conf_text: str, key: str) -> str | None:
    for line in conf_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            raw = stripped.split("=", 1)[1].strip()
            raw = raw.strip('"').strip("'")
            return raw if raw else None
    return None


def _validate_runner_conf(conf_path: Path, config: RealBacktestReadinessConfig, sandbox_dir: Path) -> list[str]:
    errors: list[str] = []
    if not conf_path.is_file():
        return [f"Runner config not found: {conf_path}"]
    conf_text = conf_path.read_text(encoding="utf-8")

    ea_source = _parse_conf_value(conf_text, "EA_SOURCE")
    if not ea_source:
        errors.append("EA_SOURCE is missing or empty in runner config")
    else:
        ea_source_path = Path(ea_source)
        if not ea_source_path.is_absolute():
            ea_source_path = REPO_ROOT / ea_source_path
        ea_source_resolved = ea_source_path.resolve()
        sandbox_str = str(sandbox_dir.resolve())
        if not str(ea_source_resolved).startswith(sandbox_str + "/") and str(ea_source_resolved) != sandbox_str:
            errors.append(f"EA_SOURCE {ea_source} is not under the implementation sandbox {sandbox_dir}")
        if not ea_source_resolved.is_file():
            errors.append(f"EA_SOURCE file does not exist: {ea_source}")
        strategies_root = str((REPO_ROOT / "automated" / "strategies").resolve())
        if str(ea_source_resolved).startswith(strategies_root + "/") or str(ea_source_resolved) == strategies_root:
            errors.append(f"EA_SOURCE must not be under automated/strategies/: {ea_source}")

    broker = _parse_conf_value(conf_text, "BROKER")
    if broker and broker.lower() not in ALLOWED_BROKER_VALUES:
        errors.append(f"BROKER must be one of {sorted(ALLOWED_BROKER_VALUES)}; got {broker!r}")

    symbol = _parse_conf_value(conf_text, "SYMBOL")
    if symbol and config.expected_symbol and symbol != config.expected_symbol:
        errors.append(f"SYMBOL mismatch: got {symbol!r}, expected {config.expected_symbol!r}")

    timeframe = _parse_conf_value(conf_text, "TIMEFRAME")
    if timeframe and config.expected_timeframe and timeframe != config.expected_timeframe:
        errors.append(f"TIMEFRAME mismatch: got {timeframe!r}, expected {config.expected_timeframe!r}")

    ea_set_file = _parse_conf_value(conf_text, "EA_SET_FILE")
    if config.set_file_path and ea_set_file:
        provided_set = Path(config.set_file_path).resolve()
        conf_set = Path(ea_set_file)
        if not conf_set.is_absolute():
            conf_set = (conf_path.parent / conf_set).resolve()
        else:
            conf_set = conf_set.resolve()
        if provided_set != conf_set:
            errors.append(f"EA_SET_FILE mismatch: config has {ea_set_file}, but provided set_file_path is {config.set_file_path}")

    return errors


def _compute_readiness_input_digests(
    mq5_path: Path | None,
    conf_path: Path | None = None,
    set_path: Path | None = None,
    config_file_path: str | Path | None = None,
) -> dict[str, str]:
    digests: dict[str, str] = {}
    if mq5_path and mq5_path.is_file():
        digests["generated_mq5"] = file_sha256(mq5_path)
    if conf_path and conf_path.is_file():
        digests["generated_conf"] = file_sha256(conf_path)
    if set_path and set_path.is_file():
        digests["generated_set"] = file_sha256(set_path)
    if config_file_path:
        cp = Path(config_file_path).resolve()
        if cp.is_file():
            digests["readiness_config"] = file_sha256(cp)
    return digests


def run_real_backtest_readiness(
    db_path: str | Path,
    impl_request_id: str,
    config: RealBacktestReadinessConfig,
    *,
    runner_conf_override: str | Path | None = None,
    set_file_override: str | Path | None = None,
    runner_script: str | Path | None = None,
    readiness_config_path: str | Path | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    _strategy_id: str | None = None
    _version: str | None = None
    _impl_id: str | None = None

    def _result(
        status: str,
        errors: list[str],
        *,
        implementation_id: str | None = None,
        runner_conf_path: str | None = None,
        set_file_path: str | None = None,
        command_display: str | None = None,
        exit_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        report_paths: list[str] | None = None,
        log_paths: list[str] | None = None,
        input_digests: dict[str, str] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        result = {
            "mode": "real_backtest_readiness",
            "status": status,
            "errors": errors,
            "impl_request_id": impl_request_id,
            "strategy_id": _strategy_id,
            "version": _version,
            "implementation_id": implementation_id,
            "runner_conf_path": runner_conf_path,
            "set_file_path": set_file_path,
            "command_display": command_display or "",
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "report_paths": report_paths or [],
            "log_paths": log_paths or [],
            "input_digests": input_digests or {},
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration_seconds": (datetime.now(timezone.utc) - started_at).total_seconds(),
            "timeout_seconds": config.timeout_seconds,
        }
        result.update(extra)
        return result

    request = registry.get_implementation_request(db_path, impl_request_id)
    if not request:
        return _result(
            "failed",
            [f"Implementation request not found: {impl_request_id}"],
            input_digests=_compute_readiness_input_digests(None, config_file_path=readiness_config_path),
        )

    _strategy_id = request["strategy_id"]
    _version = request["strategy_version"]
    sandbox_dir = Path(request["sandbox_dir"])

    try:
        impl_mod.assert_sandbox_path(sandbox_dir)
    except SchemaValidationError as exc:
        return _result(
            "failed", [str(exc)],
            input_digests=_compute_readiness_input_digests(None, config_file_path=readiness_config_path),
        )

    implementations = registry.list_implementations(db_path, impl_request_id)
    if not implementations:
        return _result(
            "failed", ["No implementation records found; run compile-check first"],
            input_digests=_compute_readiness_input_digests(None, config_file_path=readiness_config_path),
        )

    current_impl = implementations[-1]
    compile_status = current_impl.get("compile_status")
    if compile_status not in ("mock_checked", "passed"):
        return _result(
            "failed",
            [f"Compile status must be mock_checked or passed before readiness check; got {compile_status!r}"],
            implementation_id=current_impl.get("implementation_id"),
            input_digests=_compute_readiness_input_digests(None, config_file_path=readiness_config_path),
        )

    _impl_id = current_impl.get("implementation_id")
    generated_mq5_raw = current_impl.get("generated_mq5_path")
    mq5_path: Path | None = None
    if generated_mq5_raw:
        mq5_path = Path(generated_mq5_raw)
        try:
            impl_mod.assert_sandbox_path(mq5_path)
        except SchemaValidationError as exc:
            return _result(
                "failed", [f"Generated .mq5 path violation: {exc}"],
                implementation_id=_impl_id,
                input_digests=_compute_readiness_input_digests(None, config_file_path=readiness_config_path),
            )
        forbidden = impl_mod.check_no_production_touch([mq5_path])
        if forbidden:
            return _result(
                "failed", forbidden,
                implementation_id=_impl_id,
                input_digests=_compute_readiness_input_digests(None, config_file_path=readiness_config_path),
            )

    if mq5_path and not mq5_path.is_file():
        return _result(
            "failed", [f"Generated .mq5 file not found: {mq5_path}"],
            implementation_id=_impl_id,
            input_digests=_compute_readiness_input_digests(None, config_file_path=readiness_config_path),
        )

    runner_conf_path: Path | None = None
    if runner_conf_override:
        runner_conf_path = Path(runner_conf_override)
    elif config.runner_conf_path:
        runner_conf_path = Path(config.runner_conf_path)
    if runner_conf_path is None or not runner_conf_path.is_file():
        return _result(
            "failed", [f"Runner config not found: {runner_conf_path}"],
            implementation_id=_impl_id,
            input_digests=_compute_readiness_input_digests(mq5_path, config_file_path=readiness_config_path),
        )

    set_file_path: Path | None = None
    if set_file_override:
        set_file_path = Path(set_file_override)
    elif config.set_file_path:
        set_file_path = Path(config.set_file_path)
    if set_file_path is None or not set_file_path.is_file():
        return _result(
            "failed", [f"Set file not found: {set_file_path}"],
            implementation_id=_impl_id,
            input_digests=_compute_readiness_input_digests(mq5_path, conf_path=runner_conf_path, config_file_path=readiness_config_path),
        )

    conf_errors = _validate_runner_conf(runner_conf_path, config, sandbox_dir)
    if conf_errors:
        return _result(
            "failed", conf_errors,
            implementation_id=_impl_id,
            runner_conf_path=str(runner_conf_path.resolve()),
            set_file_path=str(set_file_path.resolve()),
            input_digests=_compute_readiness_input_digests(mq5_path, conf_path=runner_conf_path, set_path=set_file_path, config_file_path=readiness_config_path),
        )

    output_dir = Path(config.output_dir)
    strategies_root = REPO_ROOT / "automated" / "strategies"
    if str(output_dir.resolve()).startswith(str(strategies_root.resolve()) + "/") or str(output_dir.resolve()) == str(strategies_root.resolve()):
        return _result(
            "failed", [f"Output directory must not be under automated/strategies/: {output_dir}"],
            implementation_id=_impl_id,
            runner_conf_path=str(runner_conf_path.resolve()),
            set_file_path=str(set_file_path.resolve()),
            input_digests=_compute_readiness_input_digests(mq5_path, conf_path=runner_conf_path, set_path=set_file_path, config_file_path=readiness_config_path),
        )

    runner_script_path: Path
    if runner_script is not None:
        runner_script_path = Path(runner_script)
    else:
        runner_script_path = REPO_ROOT / "automated" / "scripts" / "run_backtest.sh"
    if not runner_script_path.is_file():
        return _result(
            "failed", [f"Runner script not found: {runner_script_path}"],
            implementation_id=_impl_id,
            runner_conf_path=str(runner_conf_path.resolve()),
            set_file_path=str(set_file_path.resolve()),
            input_digests=_compute_readiness_input_digests(mq5_path, conf_path=runner_conf_path, set_path=set_file_path, config_file_path=readiness_config_path),
        )

    wine_binary = shutil.which(config.wine_binary)
    if not wine_binary and config.wine_binary != "wine":
        return _result(
            "failed", [f"Wine binary not found: {config.wine_binary}"],
            implementation_id=_impl_id,
            runner_conf_path=str(runner_conf_path.resolve()),
            set_file_path=str(set_file_path.resolve()),
            input_digests=_compute_readiness_input_digests(mq5_path, conf_path=runner_conf_path, set_path=set_file_path, config_file_path=readiness_config_path),
        )

    cmd = [str(runner_script_path), str(runner_conf_path.resolve())]
    cmd_display = " ".join(str(c) for c in cmd)

    output_dir.mkdir(parents=True, exist_ok=True)

    input_digests = _compute_readiness_input_digests(mq5_path, conf_path=runner_conf_path, set_path=set_file_path, config_file_path=readiness_config_path)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        status = "passed" if exit_code == 0 else "failed"
        errors: list[str] = []
        if status == "failed":
            errors.append(f"Runner exited with code {exit_code}")
    except subprocess.TimeoutExpired as exc:
        return _result(
            "timed_out",
            [f"Backtest readiness timed out after {config.timeout_seconds}s"],
            implementation_id=_impl_id,
            runner_conf_path=str(runner_conf_path.resolve()),
            set_file_path=str(set_file_path.resolve()),
            command_display=cmd_display,
            stdout=(exc.stdout or "") if hasattr(exc, "stdout") else "",
            stderr=(exc.stderr or "") if hasattr(exc, "stderr") else "",
            input_digests=input_digests,
        )
    except FileNotFoundError:
        return _result(
            "failed",
            [f"Runner script executable not found: {cmd[0]}"],
            implementation_id=_impl_id,
            runner_conf_path=str(runner_conf_path.resolve()),
            set_file_path=str(set_file_path.resolve()),
            command_display=cmd_display,
            input_digests=input_digests,
        )

    finished_at = datetime.now(timezone.utc)
    duration = (finished_at - started_at).total_seconds()

    report_paths: list[str] = []
    log_paths: list[str] = []

    conf_text = runner_conf_path.read_text(encoding="utf-8") if runner_conf_path.is_file() else ""
    runner_run_id = _parse_conf_value(conf_text, "RUN_ID")
    if not runner_run_id:
        runner_run_id = "unknown"

    runner_report_dir = REPO_ROOT / "automated" / "reports" / runner_run_id
    if runner_report_dir.is_dir():
        for fname in ["trades.csv", "equity.csv", "bars.csv", "run_summary.json", "mt5_report.htm"]:
            fpath = runner_report_dir / fname
            if fpath.is_file():
                dest = output_dir / fname
                shutil.copy2(fpath, dest)
                report_paths.append(str(dest.resolve()))
        for fname in ["terminal_run.log", "tester_agent.log", "compile.log"]:
            fpath = runner_report_dir / fname
            if fpath.is_file():
                dest = output_dir / fname
                shutil.copy2(fpath, dest)
                log_paths.append(str(dest.resolve()))

    return {
        "mode": "real_backtest_readiness",
        "status": status,
        "errors": errors,
        "impl_request_id": impl_request_id,
        "implementation_id": _impl_id,
        "strategy_id": _strategy_id,
        "version": _version,
        "runner_conf_path": str(runner_conf_path.resolve()),
        "set_file_path": str(set_file_path.resolve()),
        "command_display": cmd_display,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "report_paths": report_paths,
        "log_paths": log_paths,
        "input_digests": input_digests,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "duration_seconds": duration,
        "timeout_seconds": config.timeout_seconds,
    }
