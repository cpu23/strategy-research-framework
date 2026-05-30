from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hashing import file_sha256, stable_hash
from .registry import insert_dataset, utc_now


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()


def inspect_bars_csv(path: str | Path) -> dict[str, Any]:
    bars_path = Path(path)
    digest = file_sha256(bars_path)
    row_count = 0
    start_ts = None
    end_ts = None
    with bars_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if "time" not in (reader.fieldnames or []):
            raise ValueError(f"{bars_path} does not contain a time column")
        for row in reader:
            timestamp = row["time"]
            row_count += 1
            if start_ts is None:
                start_ts = timestamp
            end_ts = timestamp
    if row_count == 0 or start_ts is None or end_ts is None:
        raise ValueError(f"{bars_path} does not contain any bar rows")
    return {
        "file_path": str(bars_path),
        "file_hash": digest,
        "row_count": row_count,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }


def build_dataset_metadata(
    *,
    bars_path: str | Path,
    symbol: str,
    timeframe: str,
    source_type: str = "mt5_export",
    source_name: str = "MT5",
    broker: str | None = None,
    server: str | None = None,
    timezone_name: str = "broker_server",
    missing_data_policy: str = "not_evaluated_phase1",
    cleaning_rules: str = "raw_mt5_export_no_cleaning",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inspected = inspect_bars_csv(bars_path)
    dataset_id = f"DATA_{_slug(symbol)}_{_slug(timeframe)}_{inspected['file_hash'][:12].upper()}"
    exported_at = datetime.fromtimestamp(Path(bars_path).stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    return {
        "dataset_id": dataset_id,
        "source_type": source_type,
        "source_name": source_name,
        "broker": broker,
        "server": server,
        "symbol": symbol,
        "timeframe": timeframe,
        "start_ts": inspected["start_ts"],
        "end_ts": inspected["end_ts"],
        "row_count": inspected["row_count"],
        "file_path": inspected["file_path"],
        "file_hash": inspected["file_hash"],
        "exported_at": exported_at,
        "timezone": timezone_name,
        "missing_data_policy": missing_data_policy,
        "cleaning_rules": cleaning_rules,
        "created_at": utc_now(),
        "metadata_json": json.dumps(metadata or {}, sort_keys=True),
    }


def register_dataset(db_path: str | Path, **kwargs: Any) -> dict[str, Any]:
    dataset = build_dataset_metadata(**kwargs)
    insert_dataset(db_path, dataset)
    return dataset


def dataset_bundle_hash(component_hashes: list[str]) -> str:
    return stable_hash({"component_dataset_hashes": sorted(component_hashes)})
