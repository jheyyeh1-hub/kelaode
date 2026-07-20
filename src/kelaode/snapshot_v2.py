"""Deterministic, provider-independent snapshot-v2 packaging primitives."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

CSV_HEADER = ("date", "open", "high", "low", "close", "volume")
SERIALIZER_VERSION = "snapshot-csv-v2.0"
MANIFEST_SCHEMA_VERSION = "snapshot-manifest-v2.0"
PACKAGE_SCHEMA_VERSION = "snapshot-package-v2.0"
ARCHIVE_MEMBERS = ("manifest.json",)  # CSV members precede this in universe order.
FIXED_MTIME = 0


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        raise ValueError("datetime is not a supported market date")
    try:
        parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid market date: {value!r}") from exc
    return parsed.isoformat()


def _number(value: Any) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError("missing and boolean numeric values are unsupported")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"unsupported numeric value: {value!r}") from exc
    if not number.is_finite():
        raise ValueError("non-finite numeric values are unsupported")
    # Fixed, locale-independent scale; forbids rounding hidden input precision.
    if number != number.quantize(Decimal("0.00000001")):
        raise ValueError("numeric values may have at most 8 decimal places")
    return format(number, ".8f")


def serialize_market_rows(rows: Iterable[Mapping[str, Any]]) -> bytes:
    """Serialize normalized OHLCV rows to canonical UTF-8/LF CSV bytes."""
    normalized: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for raw in rows:
        if set(raw) != set(CSV_HEADER):
            raise ValueError("market row has unknown or missing fields")
        day = _date(raw["date"])
        if day in seen:
            raise ValueError(f"duplicate date: {day}")
        seen.add(day)
        normalized.append((day, *(_number(raw[name]) for name in CSV_HEADER[1:])))
    normalized.sort(key=lambda item: item[0])
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    writer.writerows(normalized)
    return output.getvalue().encode("utf-8")


def write_market_csv(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    Path(path).write_bytes(serialize_market_rows(rows))


def validate_market_csv(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV is not UTF-8") from exc
    if b"\r" in data or not data.endswith(b"\n"):
        raise ValueError("CSV must use LF and end with LF")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != CSV_HEADER:
        raise ValueError("CSV header or column order is invalid")
    rows = list(reader)
    canonical = serialize_market_rows(rows)
    if canonical != data:
        raise ValueError("CSV bytes are not canonical")
    if not rows:
        raise ValueError("CSV cannot be empty")
    return {"row_count": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"]}


def build_manifest(*, universe: Iterable[str], csv_root: str | Path, protocol_hash: str,
                   acquisition_config_hash: str, source_tree_fingerprint: str,
                   metadata: Mapping[str, Mapping[str, Any]],
                   acquisition_attempt_id: str) -> dict[str, Any]:
    symbols = tuple(universe)
    if len(symbols) != 9 or len(set(symbols)) != 9:
        raise ValueError("snapshot-v2 requires exactly nine unique ordered symbols")
    root = Path(csv_root)
    entries = []
    for symbol in symbols:
        allowed = {"provider", "endpoint", "frequency", "adjustment", "requested_start",
                   "requested_end", "acquisition_timestamp"}
        values = dict(metadata[symbol])
        if set(values) != allowed:
            raise ValueError(f"unknown or missing metadata for {symbol}")
        path = root / f"{symbol}.csv"
        checked = validate_market_csv(path.read_bytes())
        entries.append({"symbol": symbol, "relative_path": path.name,
                        "sha256": sha256_file(path), "byte_size": path.stat().st_size,
                        **checked, **values,
                        "actual_start": checked["first_date"], "actual_end": checked["last_date"],
                        "acquisition_attempt_id": acquisition_attempt_id,
                        "serializer_version": SERIALIZER_VERSION,
                        "schema_version": MANIFEST_SCHEMA_VERSION})
    identity_payload = {"ordered_universe": list(symbols), "protocol_hash": protocol_hash,
                        "acquisition_config_hash": acquisition_config_hash,
                        "files": [{"symbol": x["symbol"], "sha256": x["sha256"]} for x in entries]}
    manifest = {"schema_version": MANIFEST_SCHEMA_VERSION, "ordered_universe": list(symbols),
                "protocol_hash": protocol_hash, "acquisition_config_hash": acquisition_config_hash,
                "source_tree_fingerprint": source_tree_fingerprint, "all_symbol_success": True,
                "no_fallback": True, "entries": entries,
                "canonical_snapshot_identity": sha256_bytes(canonical_json_bytes(identity_payload))}
    return manifest


def write_manifest(path: str | Path, manifest: Mapping[str, Any]) -> None:
    Path(path).write_bytes(canonical_json_bytes(manifest))


def build_archive(output: str | Path, root: str | Path, universe: Iterable[str]) -> str:
    """Build a deterministic uncompressed POSIX tar containing nine CSVs + manifest."""
    root, output = Path(root), Path(output)
    names = [f"{symbol}.csv" for symbol in universe] + ["manifest.json"]
    if len(names) != 10 or len(set(names)) != 10:
        raise ValueError("archive requires nine unique CSVs and manifest.json")
    with output.open("wb") as raw, tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name in names:
            source = root / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"archive input must be a regular file: {name}")
            data = source.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), FIXED_MTIME, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    return sha256_file(output)


_SPEC_FIELDS = {"schema_version", "executable", "snapshot_name", "release_tag", "asset_name",
                "immutable_url", "archive_sha256", "manifest_sha256", "canonical_snapshot_identity",
                "expected_files", "archive_format", "cache"}


def load_package_spec(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(raw) != _SPEC_FIELDS or raw["schema_version"] != PACKAGE_SCHEMA_VERSION:
        raise ValueError("package spec has unknown/missing fields or unsupported schema")
    if raw["executable"] is not True:
        raise ValueError("placeholder package spec is explicitly non-executable")
    if raw["archive_format"] != "tar" or set(raw["cache"]) != {"offline", "directory"}:
        raise ValueError("unsupported archive format or cache rules")
    expected = raw["expected_files"]
    if len(expected) != 10 or len(expected) != len(set(expected)) or "manifest.json" not in expected:
        raise ValueError("expected_files must identify nine CSVs and manifest.json")
    for field in ("archive_sha256", "manifest_sha256", "canonical_snapshot_identity"):
        if len(raw[field]) != 64 or any(c not in "0123456789abcdef" for c in raw[field]):
            raise ValueError(f"invalid {field}")
    url = urllib.parse.urlparse(raw["immutable_url"])
    if url.scheme not in {"https", "file"} or not url.path:
        raise ValueError("immutable_url must be a pinned HTTPS or local-test file URL")
    return raw


def _safe_member(member: tarfile.TarInfo, seen: set[str]) -> str:
    name = member.name
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or name in seen:
        raise ValueError("unsafe or duplicate archive member")
    if not member.isfile() or member.issym() or member.islnk() or len(pure.parts) != 1:
        raise ValueError("archive members must be top-level regular files")
    seen.add(name)
    return name


def _obtain_archive(spec: Mapping[str, Any], staging: Path) -> Path:
    cache_dir = Path(spec["cache"]["directory"]).expanduser()
    cached = cache_dir / f'{spec["archive_sha256"]}.tar'
    if cached.is_file() and sha256_file(cached) == spec["archive_sha256"]:
        return cached
    if spec["cache"]["offline"]:
        raise ValueError("archive is absent from verified offline cache")
    target = staging / "download.tar"
    with urllib.request.urlopen(spec["immutable_url"], timeout=30) as response, target.open("wb") as out:
        shutil.copyfileobj(response, out)
    if sha256_file(target) != spec["archive_sha256"]:
        raise ValueError("archive SHA-256 mismatch")
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary = cache_dir / f".{cached.name}.{os.getpid()}"
    shutil.copyfile(target, temporary)
    os.replace(temporary, cached)
    return cached


def materialize(spec_path: str | Path, output: str | Path) -> None:
    spec, output = load_package_spec(spec_path), Path(output)
    if output.exists():
        raise ValueError("output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    try:
        archive_path = _obtain_archive(spec, staging)
        if sha256_file(archive_path) != spec["archive_sha256"]:
            raise ValueError("archive SHA-256 mismatch")
        extracted = staging / "snapshot"
        extracted.mkdir()
        seen: set[str] = set()
        try:
            with tarfile.open(archive_path, mode="r:") as archive:
                for member in archive:
                    name = _safe_member(member, seen)
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("archive member cannot be read")
                    (extracted / name).write_bytes(stream.read())
        except (tarfile.TarError, EOFError) as exc:
            raise ValueError("corrupt or truncated archive") from exc
        if seen != set(spec["expected_files"]):
            raise ValueError("archive contains missing, extra, or renamed files")
        manifest_path = extracted / "manifest.json"
        if sha256_file(manifest_path) != spec["manifest_sha256"]:
            raise ValueError("manifest SHA-256 mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_fields = {"schema_version", "ordered_universe", "protocol_hash",
                           "acquisition_config_hash", "source_tree_fingerprint",
                           "all_symbol_success", "no_fallback", "entries",
                           "canonical_snapshot_identity"}
        if set(manifest) != manifest_fields or manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError("manifest has unknown/missing fields or unsupported schema")
        if manifest.get("canonical_snapshot_identity") != spec["canonical_snapshot_identity"]:
            raise ValueError("canonical snapshot identity mismatch")
        if manifest.get("all_symbol_success") is not True or manifest.get("no_fallback") is not True:
            raise ValueError("manifest does not declare complete no-fallback acquisition")
        entries = manifest.get("entries")
        if not isinstance(entries, list) or [x.get("symbol") for x in entries] != manifest.get("ordered_universe"):
            raise ValueError("manifest entries do not match ordered universe")
        adjustments = {x.get("adjustment") for x in entries}
        if len(adjustments) != 1:
            raise ValueError("mixed adjustment conventions are forbidden")
        entry_fields = {"symbol", "relative_path", "sha256", "byte_size", "row_count",
                        "first_date", "last_date", "provider", "endpoint", "frequency",
                        "adjustment", "requested_start", "requested_end", "actual_start",
                        "actual_end", "acquisition_timestamp", "acquisition_attempt_id",
                        "serializer_version", "schema_version"}
        for entry in entries:
            if set(entry) != entry_fields:
                raise ValueError("manifest entry has unknown or missing fields")
            if entry.get("provider") != "AKShare/Eastmoney" or entry.get("endpoint") != "fund_etf_hist_em":
                raise ValueError("wrong provider metadata")
            if (entry.get("frequency"), entry.get("adjustment"), entry.get("serializer_version"),
                    entry.get("schema_version")) != ("daily", "qfq", SERIALIZER_VERSION,
                                                      MANIFEST_SCHEMA_VERSION):
                raise ValueError("wrong frequency, adjustment, serializer, or schema metadata")
            path = extracted / entry["relative_path"]
            if path.name not in seen or sha256_file(path) != entry.get("sha256") or path.stat().st_size != entry.get("byte_size"):
                raise ValueError("CSV hash or size mismatch")
            checked = validate_market_csv(path.read_bytes())
            if (checked["row_count"], checked["first_date"], checked["last_date"]) != (
                    entry.get("row_count"), entry.get("first_date"), entry.get("last_date")):
                raise ValueError("CSV contents do not match manifest")
        identity_payload = {"ordered_universe": manifest["ordered_universe"],
                            "protocol_hash": manifest["protocol_hash"],
                            "acquisition_config_hash": manifest["acquisition_config_hash"],
                            "files": [{"symbol": x["symbol"], "sha256": x["sha256"]}
                                      for x in entries]}
        if sha256_bytes(canonical_json_bytes(identity_payload)) != spec["canonical_snapshot_identity"]:
            raise ValueError("canonical snapshot identity cannot be reproduced")
        os.replace(extracted, output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
