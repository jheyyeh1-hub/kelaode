"""Immutable market-data snapshot manifests and pre-run validation."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

SNAPSHOT_SCHEMA_VERSION = "1.0"

def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

@dataclass(frozen=True)
class SnapshotEntry:
    symbol: str
    provider: str
    endpoint: str
    adjustment: str
    requested_start: str
    requested_end: str
    actual_start: str
    actual_end: str
    row_count: int
    file_format: str
    relative_path: str
    sha256: str
    downloaded_at: str
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        missing = [name for name, value in asdict(self).items() if value in (None, "")]
        if missing:
            raise ValueError(f"snapshot source metadata is missing: {', '.join(missing)}")
        if self.row_count <= 0 or len(self.sha256) != 64:
            raise ValueError("invalid snapshot row count or SHA-256")
        if any(character not in "0123456789abcdef" for character in self.sha256.lower()):
            raise ValueError("SHA-256 must be hexadecimal")
        requested_start, requested_end, actual_start, actual_end = (
            datetime.fromisoformat(value) for value in
            (self.requested_start, self.requested_end, self.actual_start, self.actual_end))
        downloaded = datetime.fromisoformat(self.downloaded_at)
        if requested_start > requested_end or actual_start > actual_end:
            raise ValueError("snapshot date ranges are reversed")
        if downloaded.tzinfo is None:
            raise ValueError("download timestamp must include a timezone")
        relative = Path(self.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("snapshot paths must be relative and cannot traverse data_root")

@dataclass(frozen=True)
class SnapshotManifest:
    entries: tuple[SnapshotEntry, ...]
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    @classmethod
    def load(cls, path: str | Path) -> "SnapshotManifest":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(raw) != {"schema_version", "entries"}:
            raise ValueError("unknown or missing snapshot manifest fields")
        if raw["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(f"unsupported snapshot schema: {raw['schema_version']}")
        allowed = set(SnapshotEntry.__dataclass_fields__)
        entries = []
        for item in raw["entries"]:
            item = dict(item)
            if "status" in item or "error" in item:  # downloader staging manifest
                if item.pop("status", None) != "success" or item.pop("error", None) is not None:
                    raise ValueError("snapshot contains a failed download")
            if set(item) != allowed:
                raise ValueError("unknown or missing snapshot entry fields")
            entries.append(SnapshotEntry(**item))
        return cls(tuple(entries), raw["schema_version"])

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "entries": [asdict(x) for x in self.entries]}

    @property
    def hash(self) -> str:
        return hashlib.sha256(canonical_json(self.as_dict()).encode()).hexdigest()

    def validate(self, root: str | Path, *, expected_symbols=(), allow_mixed_adjustments=False) -> None:
        if not self.entries:
            raise ValueError("snapshot manifest has no entries")
        symbols = tuple(x.symbol for x in self.entries)
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in snapshot manifest")
        if expected_symbols and symbols != tuple(expected_symbols):
            raise ValueError(f"snapshot symbols/order {symbols!r} do not match universe {tuple(expected_symbols)!r}")
        adjustments = {x.adjustment for x in self.entries}
        if len(adjustments) > 1 and not allow_mixed_adjustments:
            raise ValueError("mixed adjustment conventions are forbidden")
        root = Path(root)
        for entry in self.entries:
            path = root / entry.relative_path
            if root.resolve() not in path.resolve().parents:
                raise ValueError("snapshot path escapes data_root")
            if not path.is_file():
                raise ValueError(f"snapshot file missing: {entry.relative_path}")
            if sha256_file(path) != entry.sha256:
                raise ValueError(f"snapshot SHA-256 mismatch: {entry.relative_path}")
            if entry.file_format.lower() != "csv":
                raise ValueError(f"unsupported validated fixture format: {entry.file_format}")
            with path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                required = {"date", "open", "high", "low", "close", "volume"}
                if set(reader.fieldnames or ()) != required:
                    raise ValueError(f"unexpected CSV schema: {entry.relative_path}")
                rows = list(reader)
            if len(rows) != entry.row_count:
                raise ValueError(f"snapshot row count mismatch: {entry.relative_path}")
            dates = [datetime.fromisoformat(row["date"]).date().isoformat() for row in rows]
            if len(dates) != len(set(dates)):
                raise ValueError(f"duplicate dates: {entry.relative_path}")
            if dates != sorted(dates):
                raise ValueError(f"dates are not strictly ordered: {entry.relative_path}")
            if not rows or min(dates) != entry.actual_start or max(dates) != entry.actual_end:
                raise ValueError(f"snapshot actual date range mismatch: {entry.relative_path}")
            for row in rows:
                o, h, low, close, volume = (float(row[k]) for k in ("open", "high", "low", "close", "volume"))
                if (not all(math.isfinite(x) for x in (o, h, low, close, volume)) or volume < 0 or
                        min(o, h, low, close) <= 0 or h < max(o, close) or low > min(o, close)):
                    raise ValueError(f"invalid OHLC data: {entry.relative_path} {row.get('date')}")
