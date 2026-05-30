from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    APPROVAL_SCOPES,
    APPROVAL_USAGE_STATUSES,
    ARTIFACT_TYPES,
    COMPILE_STATUSES,
    EXPERIMENT_STATUSES,
    GATE_STATUSES,
    IMPLEMENTATION_REQUEST_STATUSES,
    LIFECYCLE_TRANSITION_STATUSES,
    QUEUE_ITEM_STATUSES,
    SWEEP_STATUSES,
    SWEEP_TYPES,
    ensure_member,
    portable_path,
)
from .hashing import file_sha256
from .schemas import REPO_ROOT


DEFAULT_DB_PATH = REPO_ROOT / "automated" / "research_registry.sqlite"
SCHEMA_VERSION = 6


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    broker TEXT,
    server TEXT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    end_ts TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    timezone TEXT NOT NULL,
    missing_data_policy TEXT NOT NULL,
    cleaning_rules TEXT NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS dataset_bundles (
    dataset_bundle_id TEXT PRIMARY KEY,
    component_dataset_ids_json TEXT NOT NULL,
    bundle_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    internal_uuid TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    run_reason TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    parameter_set_hash TEXT NOT NULL,
    dataset_id TEXT,
    dataset_bundle_id TEXT,
    dataset_hash TEXT,
    dataset_bundle_hash TEXT,
    code_version TEXT NOT NULL,
    execution_config_hash TEXT NOT NULL,
    cost_config_hash TEXT NOT NULL,
    engine TEXT NOT NULL,
    implementation_files_json TEXT NOT NULL,
    implementation_mode TEXT NOT NULL,
    execution_timing_json TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    universe_json TEXT NOT NULL,
    parent_experiment_id TEXT,
    rerun_of_experiment_id TEXT,
    is_artifact_regeneration INTEGER NOT NULL DEFAULT 0,
    change_type TEXT NOT NULL,
    change_summary TEXT NOT NULL,
    rationale TEXT NOT NULL,
    parameter_diff_json TEXT,
    structural_diff_json TEXT,
    research_budget_snapshot_json TEXT NOT NULL,
    complexity_score INTEGER NOT NULL,
    min_trades_required INTEGER NOT NULL,
    cost_assumptions_documented INTEGER NOT NULL,
    dataset_metadata_present INTEGER NOT NULL,
    hypothesis_present INTEGER NOT NULL,
    validation_report_path TEXT,
    gate_status TEXT NOT NULL DEFAULT 'incomplete',
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    headline_metrics_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(dataset_id) REFERENCES datasets(dataset_id)
);

CREATE TABLE IF NOT EXISTS experiment_metrics (
    experiment_id TEXT NOT NULL,
    period_type TEXT NOT NULL,
    net_return REAL,
    cagr REAL,
    sharpe REAL,
    sortino REAL,
    max_drawdown REAL,
    calmar REAL,
    win_rate REAL,
    avg_trade REAL,
    median_trade REAL,
    profit_factor REAL,
    exposure_time REAL,
    turnover REAL,
    trade_count INTEGER,
    best_trade_pct_of_total REAL,
    cost_sensitivity_score REAL,
    parameter_stability_score REAL,
    correlation_to_portfolio REAL,
    notes TEXT,
    PRIMARY KEY (experiment_id, period_type),
    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS experiment_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    file_hash TEXT,
    created_at TEXT NOT NULL,
    artifact_regenerated INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS agent_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT,
    agent_role TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    file_hash TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS implementation_tasks (
    implementation_task_id TEXT PRIMARY KEY,
    requested_by TEXT NOT NULL DEFAULT 'unknown',
    reason TEXT NOT NULL,
    files_to_change_json TEXT NOT NULL,
    expected_behavior_change TEXT,
    expected_behaviour_change TEXT NOT NULL DEFAULT '',
    tests_required TEXT NOT NULL,
    human_approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
);

CREATE TABLE IF NOT EXISTS lifecycle_transitions (
    transition_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_spec_path TEXT,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    experiment_id TEXT,
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    reason TEXT NOT NULL,
    gate_snapshot_path TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    notes TEXT,
    override INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sweeps (
    sweep_id TEXT PRIMARY KEY,
    parent_experiment_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    sweep_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    budget_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    summary_path TEXT,
    notes TEXT,
    FOREIGN KEY(parent_experiment_id) REFERENCES experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS sweep_children (
    sweep_id TEXT NOT NULL,
    child_experiment_id TEXT NOT NULL,
    child_index INTEGER NOT NULL,
    child_role TEXT NOT NULL,
    parameter_diff_json TEXT,
    cost_diff_json TEXT,
    execution_diff_json TEXT,
    window_diff_json TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY (sweep_id, child_experiment_id),
    FOREIGN KEY(sweep_id) REFERENCES sweeps(sweep_id),
    FOREIGN KEY(child_experiment_id) REFERENCES experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS research_queue_items (
    queue_id TEXT PRIMARY KEY,
    priority INTEGER NOT NULL,
    hypothesis_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    parent_experiment_id TEXT,
    status TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    allowed_agent_roles_json TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    permissions_json TEXT NOT NULL,
    required_outputs_json TEXT NOT NULL,
    sweep_config_json TEXT,
    created_at TEXT NOT NULL,
    notes TEXT,
    source_path TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS implementation_requests (
    implementation_request_id TEXT PRIMARY KEY,
    hypothesis_id TEXT,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    strategy_spec_path TEXT,
    request_artifact_path TEXT NOT NULL,
    sandbox_dir TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS implementations (
    implementation_id TEXT PRIMARY KEY,
    implementation_request_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    generated_mq5_path TEXT NOT NULL,
    code_sha256 TEXT,
    compile_status TEXT,
    diff_review_status TEXT,
    input_match_status TEXT,
    approved_for_baseline INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    approved_at TEXT,
    baseline_experiment_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(implementation_request_id) REFERENCES implementation_requests(implementation_request_id)
);

CREATE TABLE IF NOT EXISTS research_queue_runs (
    queue_run_id TEXT PRIMARY KEY,
    queue_id TEXT NOT NULL,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    experiments_created_json TEXT NOT NULL DEFAULT '[]',
    sweeps_created_json TEXT NOT NULL DEFAULT '[]',
    artifacts_created_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    failures_json TEXT NOT NULL DEFAULT '[]',
    summary_path TEXT,
    source_path TEXT
);

CREATE TABLE IF NOT EXISTS approval_usage_records (
    usage_id TEXT PRIMARY KEY,
    implementation_id TEXT NOT NULL,
    implementation_request_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    queue_run_id TEXT,
    used_at TEXT NOT NULL,
    runner_mode TEXT NOT NULL DEFAULT 'baseline',
    status TEXT NOT NULL DEFAULT 'pending',
    FOREIGN KEY(implementation_id) REFERENCES implementations(implementation_id),
    FOREIGN KEY(implementation_request_id) REFERENCES implementation_requests(implementation_request_id)
);

CREATE TABLE IF NOT EXISTS scope_approvals (
    approval_id TEXT PRIMARY KEY,
    implementation_id TEXT NOT NULL,
    implementation_request_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    approval_scope TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    allow_reuse INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    used_at TEXT,
    used_by_experiment_id TEXT,
    scope_metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(implementation_id) REFERENCES implementations(implementation_id),
    FOREIGN KEY(implementation_request_id) REFERENCES implementation_requests(implementation_request_id)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = connect(db_path)
    try:
        with connection:
            connection.executescript(SCHEMA_SQL)
            _ensure_column(connection, "implementation_tasks", "requested_by", "TEXT NOT NULL DEFAULT 'unknown'")
            _ensure_column(connection, "implementation_tasks", "expected_behavior_change", "TEXT")
            _ensure_column(connection, "implementation_tasks", "status", "TEXT NOT NULL DEFAULT 'proposed'")
            _ensure_column(connection, "lifecycle_transitions", "strategy_spec_path", "TEXT")
            _ensure_column(connection, "implementations", "approval_scope", "TEXT NOT NULL DEFAULT 'baseline_only'")
            _ensure_column(connection, "implementations", "allow_reuse", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "approval_usage_records", "scope_approval_id", "TEXT")
            _ensure_column(connection, "scope_approvals", "used", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(connection, "scope_approvals", "used_at", "TEXT")
            _ensure_column(connection, "scope_approvals", "used_by_experiment_id", "TEXT")
            _set_schema_version(connection, "research_registry", SCHEMA_VERSION)
    finally:
        connection.close()


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _set_schema_version(connection: sqlite3.Connection, component: str, version: int) -> None:
    current = connection.execute(
        "SELECT version FROM schema_version WHERE component = ?",
        (component,),
    ).fetchone()
    if current and current["version"] == version:
        return
    connection.execute(
        """
        INSERT INTO schema_version (component, version, applied_at)
        VALUES (?, ?, ?)
        ON CONFLICT(component) DO UPDATE SET
            version = excluded.version,
            applied_at = excluded.applied_at
        """,
        (component, version, utc_now()),
    )


def get_schema_version(db_path: str | Path = DEFAULT_DB_PATH, component: str = "research_registry") -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM schema_version WHERE component = ?",
            (component,),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def insert_dataset(db_path: str | Path, dataset: dict[str, Any]) -> None:
    init_db(db_path)
    fields = [
        "dataset_id",
        "source_type",
        "source_name",
        "broker",
        "server",
        "symbol",
        "timeframe",
        "start_ts",
        "end_ts",
        "row_count",
        "file_path",
        "file_hash",
        "exported_at",
        "timezone",
        "missing_data_policy",
        "cleaning_rules",
        "created_at",
        "metadata_json",
    ]
    payload = dict(dataset)
    payload.setdefault("metadata_json", "{}")
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT OR REPLACE INTO datasets ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                payload,
            )
    finally:
        connection.close()


def get_dataset(db_path: str | Path, dataset_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def create_experiment(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    stored = dict(payload)
    stored.setdefault("internal_uuid", str(uuid.uuid4()))
    stored.setdefault("gate_status", "incomplete")
    stored.setdefault("status", "planned")
    stored.setdefault("headline_metrics_json", "{}")
    _validate_experiment_status(stored["status"])
    _validate_gate_status(stored["gate_status"])
    json_fields = [
        "implementation_files",
        "execution_timing",
        "universe",
        "parameter_diff",
        "structural_diff",
        "research_budget_snapshot",
    ]
    for key in json_fields:
        db_key = f"{key}_json"
        stored[db_key] = _json(stored.pop(key, None))
    bool_fields = [
        "is_artifact_regeneration",
        "cost_assumptions_documented",
        "dataset_metadata_present",
        "hypothesis_present",
    ]
    for key in bool_fields:
        stored[key] = 1 if stored.get(key) else 0

    fields = [
        "experiment_id",
        "internal_uuid",
        "hypothesis_id",
        "strategy_id",
        "strategy_version",
        "run_reason",
        "created_by",
        "created_at",
        "spec_hash",
        "parameter_set_hash",
        "dataset_id",
        "dataset_bundle_id",
        "dataset_hash",
        "dataset_bundle_hash",
        "code_version",
        "execution_config_hash",
        "cost_config_hash",
        "engine",
        "implementation_files_json",
        "implementation_mode",
        "execution_timing_json",
        "timeframe",
        "universe_json",
        "parent_experiment_id",
        "rerun_of_experiment_id",
        "is_artifact_regeneration",
        "change_type",
        "change_summary",
        "rationale",
        "parameter_diff_json",
        "structural_diff_json",
        "research_budget_snapshot_json",
        "complexity_score",
        "min_trades_required",
        "cost_assumptions_documented",
        "dataset_metadata_present",
        "hypothesis_present",
        "validation_report_path",
        "gate_status",
        "started_at",
        "completed_at",
        "status",
        "headline_metrics_json",
    ]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO experiments ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                stored,
            )
    finally:
        connection.close()


def get_experiment(db_path: str | Path, experiment_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute("SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def attach_artifact(
    db_path: str | Path,
    experiment_id: str,
    artifact_type: str,
    path: str | Path,
    *,
    artifact_regenerated: bool = False,
) -> None:
    init_db(db_path)
    artifact_path = Path(path)
    _validate_artifact_type(artifact_type)
    digest = file_sha256(artifact_path) if artifact_path.is_file() else None
    stored_path = portable_path(artifact_path, REPO_ROOT)
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO experiment_artifacts
                (experiment_id, artifact_type, path, file_hash, created_at, artifact_regenerated)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (experiment_id, artifact_type, stored_path, digest, utc_now(), 1 if artifact_regenerated else 0),
            )
    finally:
        connection.close()


def attach_agent_artifact(
    db_path: str | Path,
    *,
    experiment_id: str | None,
    agent_role: str,
    artifact_type: str,
    path: str | Path,
) -> None:
    init_db(db_path)
    artifact_path = Path(path)
    _validate_artifact_type(artifact_type)
    digest = file_sha256(artifact_path) if artifact_path.is_file() else None
    stored_path = portable_path(artifact_path, REPO_ROOT)
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO agent_artifacts
                (experiment_id, agent_role, artifact_type, path, file_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (experiment_id, agent_role, artifact_type, stored_path, digest, utc_now()),
            )
    finally:
        connection.close()


def list_agent_artifacts(db_path: str | Path, experiment_id: str | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        if experiment_id:
            rows = connection.execute(
                "SELECT * FROM agent_artifacts WHERE experiment_id = ? ORDER BY artifact_id",
                (experiment_id,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM agent_artifacts ORDER BY artifact_id").fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def list_artifacts(db_path: str | Path, experiment_id: str) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM experiment_artifacts WHERE experiment_id = ? ORDER BY artifact_id",
            (experiment_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def upsert_experiment_metrics(db_path: str | Path, experiment_id: str, metrics: dict[str, Any]) -> None:
    init_db(db_path)
    payload = {"experiment_id": experiment_id, **metrics}
    fields = [
        "experiment_id",
        "period_type",
        "net_return",
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "win_rate",
        "avg_trade",
        "median_trade",
        "profit_factor",
        "exposure_time",
        "turnover",
        "trade_count",
        "best_trade_pct_of_total",
        "cost_sensitivity_score",
        "parameter_stability_score",
        "correlation_to_portfolio",
        "notes",
    ]
    update_fields = [field for field in fields if field not in {"experiment_id", "period_type"}]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO experiment_metrics ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                ON CONFLICT(experiment_id, period_type) DO UPDATE SET
                {", ".join(f"{field} = excluded.{field}" for field in update_fields)}
                """,
                payload,
            )
    finally:
        connection.close()


def get_experiment_metrics(db_path: str | Path, experiment_id: str, period_type: str = "full") -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM experiment_metrics WHERE experiment_id = ? AND period_type = ?",
            (experiment_id, period_type),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def update_experiment(
    db_path: str | Path,
    experiment_id: str,
    **fields: Any,
) -> None:
    if not fields:
        return
    init_db(db_path)
    if "status" in fields:
        _validate_experiment_status(fields["status"])
    if "gate_status" in fields:
        _validate_gate_status(fields["gate_status"])
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [experiment_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(f"UPDATE experiments SET {assignments} WHERE experiment_id = ?", values)
    finally:
        connection.close()


def list_experiments(db_path: str | Path) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT experiment_id, strategy_id, hypothesis_id, status, gate_status, created_at
            FROM experiments
            ORDER BY created_at DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def create_lifecycle_transition(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    fields = [
        "transition_id",
        "strategy_id",
        "strategy_spec_path",
        "from_state",
        "to_state",
        "experiment_id",
        "requested_by",
        "approved_by",
        "reason",
        "gate_snapshot_path",
        "created_at",
        "status",
        "notes",
        "override",
    ]
    stored = dict(payload)
    _validate_lifecycle_transition_status(stored["status"])
    stored["override"] = 1 if stored.get("override") else 0
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO lifecycle_transitions ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                stored,
            )
    finally:
        connection.close()


def get_lifecycle_transition(db_path: str | Path, transition_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM lifecycle_transitions WHERE transition_id = ?",
            (transition_id,),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def list_lifecycle_transitions(db_path: str | Path, strategy_id: str) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT * FROM lifecycle_transitions
            WHERE strategy_id = ?
            ORDER BY created_at, transition_id
            """,
            (strategy_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def update_lifecycle_transition(db_path: str | Path, transition_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    if "status" in fields:
        _validate_lifecycle_transition_status(fields["status"])
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [transition_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(f"UPDATE lifecycle_transitions SET {assignments} WHERE transition_id = ?", values)
    finally:
        connection.close()


def create_sweep(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    stored = dict(payload)
    _validate_sweep_type(stored["sweep_type"])
    _validate_sweep_status(stored["status"])
    stored["budget_json"] = _json(stored.get("budget", stored.get("budget_json", {})))
    stored["config_json"] = _json(stored.get("config", stored.get("config_json", {})))
    fields = [
        "sweep_id",
        "parent_experiment_id",
        "strategy_id",
        "hypothesis_id",
        "sweep_type",
        "status",
        "created_by",
        "created_at",
        "completed_at",
        "budget_json",
        "config_json",
        "summary_path",
        "notes",
    ]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO sweeps ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                stored,
            )
    finally:
        connection.close()


def get_sweep(db_path: str | Path, sweep_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute("SELECT * FROM sweeps WHERE sweep_id = ?", (sweep_id,)).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def list_sweeps(db_path: str | Path) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT sweep_id, parent_experiment_id, strategy_id, sweep_type, status, created_at, completed_at, summary_path
            FROM sweeps
            ORDER BY created_at DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def update_sweep(db_path: str | Path, sweep_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    if "status" in fields:
        _validate_sweep_status(fields["status"])
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [sweep_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(f"UPDATE sweeps SET {assignments} WHERE sweep_id = ?", values)
    finally:
        connection.close()


def add_sweep_child(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    stored = dict(payload)
    _validate_experiment_status(stored["status"])
    for key in ["parameter_diff", "cost_diff", "execution_diff", "window_diff"]:
        stored[f"{key}_json"] = _json(stored.pop(key, None))
    fields = [
        "sweep_id",
        "child_experiment_id",
        "child_index",
        "child_role",
        "parameter_diff_json",
        "cost_diff_json",
        "execution_diff_json",
        "window_diff_json",
        "status",
    ]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO sweep_children ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                stored,
            )
    finally:
        connection.close()


def list_sweep_children(db_path: str | Path, sweep_id: str) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM sweep_children WHERE sweep_id = ? ORDER BY child_index",
            (sweep_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def update_sweep_child(db_path: str | Path, sweep_id: str, child_experiment_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    if "status" in fields:
        _validate_experiment_status(fields["status"])
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [sweep_id, child_experiment_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"UPDATE sweep_children SET {assignments} WHERE sweep_id = ? AND child_experiment_id = ?",
                values,
            )
    finally:
        connection.close()


def upsert_queue_item(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    stored = dict(payload)
    _validate_queue_item_status(stored["status"])
    for key in ["allowed_agent_roles", "budget", "permissions", "required_outputs", "sweep_config"]:
        stored[f"{key}_json"] = _json(stored.pop(key, None))
    stored.setdefault("updated_at", utc_now())
    fields = [
        "queue_id",
        "priority",
        "hypothesis_id",
        "strategy_id",
        "task_type",
        "parent_experiment_id",
        "status",
        "requested_by",
        "allowed_agent_roles_json",
        "budget_json",
        "permissions_json",
        "required_outputs_json",
        "sweep_config_json",
        "created_at",
        "notes",
        "source_path",
        "updated_at",
    ]
    update_fields = [field for field in fields if field != "queue_id"]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO research_queue_items ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                ON CONFLICT(queue_id) DO UPDATE SET
                {", ".join(f"{field} = excluded.{field}" for field in update_fields)}
                """,
                stored,
            )
    finally:
        connection.close()


def update_queue_item(db_path: str | Path, queue_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    stored = dict(fields)
    if "status" in stored:
        _validate_queue_item_status(stored["status"])
    for key in ["allowed_agent_roles", "budget", "permissions", "required_outputs", "sweep_config"]:
        if key in stored:
            stored[f"{key}_json"] = _json(stored.pop(key))
    stored["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in stored)
    values = list(stored.values()) + [queue_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(f"UPDATE research_queue_items SET {assignments} WHERE queue_id = ?", values)
    finally:
        connection.close()


def get_queue_item(db_path: str | Path, queue_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute("SELECT * FROM research_queue_items WHERE queue_id = ?", (queue_id,)).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def create_queue_run(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    stored = dict(payload)
    _validate_queue_item_status(stored["status"])
    stored.setdefault("summary_path", None)
    stored.setdefault("source_path", None)
    for key in ["experiments_created", "sweeps_created", "artifacts_created", "warnings", "failures"]:
        stored[f"{key}_json"] = _json(stored.pop(key, []))
    fields = [
        "queue_run_id",
        "queue_id",
        "status",
        "mode",
        "started_at",
        "completed_at",
        "experiments_created_json",
        "sweeps_created_json",
        "artifacts_created_json",
        "warnings_json",
        "failures_json",
        "summary_path",
        "source_path",
    ]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO research_queue_runs ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                stored,
            )
    finally:
        connection.close()


def update_queue_run(db_path: str | Path, queue_run_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    stored = dict(fields)
    if "status" in stored:
        _validate_queue_item_status(stored["status"])
    for key in ["experiments_created", "sweeps_created", "artifacts_created", "warnings", "failures"]:
        if key in stored:
            stored[f"{key}_json"] = _json(stored.pop(key))
    assignments = ", ".join(f"{key} = ?" for key in stored)
    values = list(stored.values()) + [queue_run_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(f"UPDATE research_queue_runs SET {assignments} WHERE queue_run_id = ?", values)
    finally:
        connection.close()


def get_queue_run(db_path: str | Path, queue_run_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute("SELECT * FROM research_queue_runs WHERE queue_run_id = ?", (queue_run_id,)).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def list_queue_runs(db_path: str | Path, queue_id: str | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        if queue_id:
            rows = connection.execute(
                "SELECT * FROM research_queue_runs WHERE queue_id = ? ORDER BY started_at, queue_run_id",
                (queue_id,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM research_queue_runs ORDER BY started_at, queue_run_id").fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def _validate_artifact_type(artifact_type: str) -> None:
    ensure_member(artifact_type, ARTIFACT_TYPES, "artifact_type")


def _validate_experiment_status(status: str) -> None:
    ensure_member(status, EXPERIMENT_STATUSES, "experiment.status")


def _validate_gate_status(status: str) -> None:
    ensure_member(status, GATE_STATUSES, "experiment.gate_status")


def _validate_lifecycle_transition_status(status: str) -> None:
    ensure_member(status, LIFECYCLE_TRANSITION_STATUSES, "lifecycle_transition.status")


def _validate_sweep_type(sweep_type: str) -> None:
    ensure_member(sweep_type, SWEEP_TYPES, "sweep.sweep_type")


def _validate_sweep_status(status: str) -> None:
    ensure_member(status, SWEEP_STATUSES, "sweep.status")


def _validate_queue_item_status(status: str) -> None:
    ensure_member(status, QUEUE_ITEM_STATUSES, "research_queue.status")


def insert_implementation_request(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    _validate_implementation_request_status(payload["status"])
    payload.setdefault("updated_at", utc_now())
    fields = [
        "implementation_request_id",
        "hypothesis_id",
        "strategy_id",
        "strategy_version",
        "strategy_spec_path",
        "request_artifact_path",
        "sandbox_dir",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    ]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT OR REPLACE INTO implementation_requests ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                payload,
            )
    finally:
        connection.close()


def get_implementation_request(db_path: str | Path, req_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM implementation_requests WHERE implementation_request_id = ?",
            (req_id,),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def update_implementation_request(db_path: str | Path, req_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    stored = dict(fields)
    if "status" in stored:
        _validate_implementation_request_status(stored["status"])
    stored["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in stored)
    values = list(stored.values()) + [req_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"UPDATE implementation_requests SET {assignments} WHERE implementation_request_id = ?",
                values,
            )
    finally:
        connection.close()


def list_implementation_requests(db_path: str | Path) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT implementation_request_id, strategy_id, strategy_version, status, created_by, created_at, updated_at
            FROM implementation_requests
            ORDER BY created_at DESC
            """
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def insert_implementation(db_path: str | Path, payload: dict[str, Any]) -> None:
    init_db(db_path)
    if payload.get("compile_status"):
        _validate_compile_status(payload["compile_status"])
    fields = [
        "implementation_id",
        "implementation_request_id",
        "strategy_id",
        "strategy_version",
        "generated_mq5_path",
        "code_sha256",
        "compile_status",
        "diff_review_status",
        "input_match_status",
        "approved_for_baseline",
        "approved_by",
        "approved_at",
        "baseline_experiment_id",
        "created_at",
    ]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT OR REPLACE INTO implementations ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                payload,
            )
    finally:
        connection.close()


def get_implementation(db_path: str | Path, impl_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM implementations WHERE implementation_id = ?",
            (impl_id,),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def update_implementation(db_path: str | Path, impl_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    stored = dict(fields)
    if "compile_status" in stored:
        _validate_compile_status(stored["compile_status"])
    assignments = ", ".join(f"{key} = ?" for key in stored)
    values = list(stored.values()) + [impl_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"UPDATE implementations SET {assignments} WHERE implementation_id = ?",
                values,
            )
    finally:
        connection.close()


def list_implementations(db_path: str | Path, req_id: str) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM implementations WHERE implementation_request_id = ? ORDER BY created_at",
            (req_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def _validate_implementation_request_status(status: str) -> None:
    ensure_member(status, IMPLEMENTATION_REQUEST_STATUSES, "implementation_request.status")


def find_implementation_request(db_path: str | Path, strategy_id: str, strategy_version: str) -> dict[str, Any] | None:
    """Find the latest implementation request for a strategy_id + version, if any."""
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM implementation_requests WHERE strategy_id = ? AND strategy_version = ? ORDER BY created_at DESC LIMIT 1",
            (strategy_id, strategy_version),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def create_approval_usage(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(db_path)
    if payload.get("status"):
        _validate_approval_usage_status(payload["status"])
    fields = [
        "usage_id",
        "implementation_id",
        "implementation_request_id",
        "experiment_id",
        "queue_run_id",
        "used_at",
        "runner_mode",
        "status",
        "scope_approval_id",
    ]
    stored = {k: payload.get(k) for k in fields}
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"""
                INSERT INTO approval_usage_records ({", ".join(fields)})
                VALUES ({", ".join(":" + field for field in fields)})
                """,
                stored,
            )
    finally:
        connection.close()
    return dict(stored)


def get_approval_usage(db_path: str | Path, usage_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM approval_usage_records WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def update_approval_usage(db_path: str | Path, usage_id: str, **fields: Any) -> None:
    if not fields:
        return
    init_db(db_path)
    stored = dict(fields)
    if "status" in stored:
        _validate_approval_usage_status(stored["status"])
    assignments = ", ".join(f"{key} = ?" for key in stored)
    values = list(stored.values()) + [usage_id]
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"UPDATE approval_usage_records SET {assignments} WHERE usage_id = ?",
                values,
            )
    finally:
        connection.close()


def list_approval_usage_for_implementation(db_path: str | Path, implementation_id: str) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM approval_usage_records WHERE implementation_id = ? ORDER BY used_at",
            (implementation_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def count_approval_usage_for_implementation(db_path: str | Path, implementation_id: str) -> int:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) as cnt FROM approval_usage_records WHERE implementation_id = ?",
            (implementation_id,),
        ).fetchone()
    finally:
        connection.close()
    return row["cnt"] if row else 0


def create_scope_approval(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    init_db(db_path)
    fields = [
        "approval_id", "implementation_id", "implementation_request_id",
        "strategy_id", "strategy_version", "approval_scope",
        "approved_by", "approved_at", "allow_reuse", "scope_metadata_json",
        "created_at",
    ]
    stored = {k: payload.get(k) for k in fields}
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                f"INSERT INTO scope_approvals ({', '.join(fields)}) VALUES ({', '.join(':' + f for f in fields)})",
                stored,
            )
    finally:
        connection.close()
    return dict(stored)


def get_scope_approval(db_path: str | Path, approval_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT * FROM scope_approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def find_scope_approval(
    db_path: str | Path,
    strategy_id: str,
    strategy_version: str,
    approval_scope: str,
) -> dict[str, Any] | None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT sa.*, COUNT(aur.usage_id) as usage_count
            FROM scope_approvals sa
            LEFT JOIN approval_usage_records aur ON aur.scope_approval_id = sa.approval_id
            WHERE sa.strategy_id = ? AND sa.strategy_version = ? AND sa.approval_scope = ?
            GROUP BY sa.approval_id
            ORDER BY sa.created_at DESC
            """,
            (strategy_id, strategy_version, approval_scope),
        ).fetchall()
    finally:
        connection.close()
    results = [dict(row) for row in rows]
    for r in results:
        r["allow_reuse"] = bool(r["allow_reuse"])
    return results[0] if results else None


def update_scope_approval_used(
    db_path: str | Path,
    approval_id: str,
    experiment_id: str,
) -> None:
    init_db(db_path)
    connection = connect(db_path)
    try:
        with connection:
            connection.execute(
                "UPDATE scope_approvals SET used = 1, used_at = ?, used_by_experiment_id = ? WHERE approval_id = ?",
                (utc_now(), experiment_id, approval_id),
            )
    finally:
        connection.close()


def list_scope_approvals(db_path: str | Path, implementation_id: str) -> list[dict[str, Any]]:
    init_db(db_path)
    connection = connect(db_path)
    try:
        rows = connection.execute(
            "SELECT * FROM scope_approvals WHERE implementation_id = ? ORDER BY created_at",
            (implementation_id,),
        ).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def count_scope_approval_usages(db_path: str | Path, approval_id: str) -> int:
    init_db(db_path)
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) as cnt FROM approval_usage_records WHERE scope_approval_id = ?",
            (approval_id,),
        ).fetchone()
    finally:
        connection.close()
    return row["cnt"] if row else 0


def _validate_approval_usage_status(status: str) -> None:
    ensure_member(status, APPROVAL_USAGE_STATUSES, "approval_usage.status")


def _validate_compile_status(status: str) -> None:
    ensure_member(status, COMPILE_STATUSES, "implementation.compile_status")
