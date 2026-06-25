from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

NULL_LIKE = {"", "null", "none", "na", "n/a", "nan"}
SENSITIVE_NAME_RE = re.compile(
    r"(email|phone|ssn|social|passport|dob|birth|address|name|ip_address|credit|card)",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][0-9:.+-Z]*)?$")


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    inferred_type: str
    non_null_count: int
    null_count: int
    unique_sample_count: int
    sample_values: list[str]
    possible_key: bool = False
    possible_sensitive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataProfile:
    path: str
    format: str
    row_count: int
    columns: list[ColumnProfile]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["columns"] = [column.to_dict() for column in self.columns]
        return payload


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in NULL_LIKE


def _infer_scalar_type(values: list[Any]) -> str:
    non_null = [str(value).strip() for value in values if not _is_null(value)]
    if not non_null:
        return "string"

    def all_match(check) -> bool:
        return all(check(value) for value in non_null)

    if all_match(lambda value: value.lower() in {"true", "false", "0", "1"}):
        return "boolean"
    if all_match(lambda value: re.fullmatch(r"[-+]?\d+", value) is not None):
        return "integer"
    if all_match(lambda value: re.fullmatch(r"[-+]?(\d+(\.\d*)?|\.\d+)", value) is not None):
        return "float"
    if all_match(lambda value: DATE_RE.match(value) is not None):
        return "datetime"
    return "string"


def _profile_rows(path: Path, fmt: str, rows: list[dict[str, Any]]) -> DataProfile:
    warnings: list[str] = []
    if not rows:
        return DataProfile(
            path=str(path), format=fmt, row_count=0, columns=[], warnings=["No rows found."]
        )

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    columns: list[ColumnProfile] = []
    for name in fieldnames:
        values = [row.get(name) for row in rows]
        null_count = sum(1 for value in values if _is_null(value))
        non_null_values = [value for value in values if not _is_null(value)]
        sample_counter = Counter(str(value) for value in non_null_values)
        sample_values = list(sample_counter.keys())[:5]
        unique_count = len(sample_counter)
        possible_key = bool(non_null_values) and unique_count == len(non_null_values)
        possible_sensitive = SENSITIVE_NAME_RE.search(name) is not None
        columns.append(
            ColumnProfile(
                name=name,
                inferred_type=_infer_scalar_type(values),
                non_null_count=len(non_null_values),
                null_count=null_count,
                unique_sample_count=unique_count,
                sample_values=sample_values,
                possible_key=possible_key,
                possible_sensitive=possible_sensitive,
            )
        )

    if any(column.possible_sensitive for column in columns):
        warnings.append("Possible sensitive columns detected from column names.")

    return DataProfile(
        path=str(path),
        format=fmt,
        row_count=len(rows),
        columns=columns,
        warnings=warnings,
    )


def _profile_csv(path: Path, max_rows: int) -> DataProfile:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        rows = [row for _, row in zip(range(max_rows), reader, strict=False)]
    return _profile_rows(path, "csv", rows)


def _coerce_json_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("rows", "data", "records", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def _profile_json(path: Path, max_rows: int) -> DataProfile:
    text = path.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
        rows = _coerce_json_rows(value)[:max_rows]
        return _profile_rows(path, "json", rows)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            if len(rows) >= max_rows:
                break
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        return _profile_rows(path, "jsonl", rows)


def profile_data_path(path: str | Path, *, max_rows: int = 1000) -> DataProfile:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        return _profile_csv(p, max_rows)
    if suffix in {".json", ".jsonl", ".ndjson"}:
        return _profile_json(p, max_rows)
    return DataProfile(
        path=str(p),
        format=suffix.lstrip(".") or "unknown",
        row_count=0,
        columns=[],
        warnings=["Unsupported data format for deterministic EDA."],
    )


def profile_data_paths(paths: list[str | Path], *, max_rows: int = 1000) -> list[DataProfile]:
    profiles: list[DataProfile] = []
    for path in paths:
        p = Path(path)
        if p.is_dir():
            files = sorted(
                child
                for child in p.iterdir()
                if child.suffix.lower() in {".csv", ".json", ".jsonl", ".ndjson"}
            )
            for child in files[:20]:
                profiles.append(profile_data_path(child, max_rows=max_rows))
            continue
        profiles.append(profile_data_path(p, max_rows=max_rows))
    return profiles


def summarize_profiles(profiles: list[DataProfile], *, max_columns: int = 12) -> str:
    if not profiles:
        return "No data samples were profiled."

    lines: list[str] = []
    for profile in profiles:
        lines.append(f"- {profile.path} ({profile.format}, sampled_rows={profile.row_count})")
        for column in profile.columns[:max_columns]:
            flags = []
            if column.possible_key:
                flags.append("possible key")
            if column.possible_sensitive:
                flags.append("possible sensitive")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"  - {column.name}: {column.inferred_type}, "
                f"nulls={column.null_count}, unique_sample={column.unique_sample_count}{suffix}"
            )
        if len(profile.columns) > max_columns:
            lines.append(f"  - ... {len(profile.columns) - max_columns} more columns")
        for warning in profile.warnings:
            lines.append(f"  - warning: {warning}")
    return "\n".join(lines)


def profiles_to_facts(profiles: list[DataProfile]) -> dict[str, Any]:
    return {
        "data_profile_count": len(profiles),
        "data_sources": [profile.path for profile in profiles],
        "formats": sorted({profile.format for profile in profiles}),
        "possible_sensitive_columns": [
            {"path": profile.path, "column": column.name}
            for profile in profiles
            for column in profile.columns
            if column.possible_sensitive
        ],
    }
