from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from kelaode.snapshot_v2 import (
    PACKAGE_SCHEMA_VERSION,
    build_archive,
    build_manifest,
    canonical_json_bytes,
    load_package_spec,
    materialize,
    serialize_market_rows,
    sha256_bytes,
    write_manifest,
)

UNIVERSE = ("510300", "510500", "159915", "512100", "512880",
            "512480", "518880", "513100", "511010")


def rows(seed=1):
    return [
        {"date": "2026-01-03", "open": seed, "high": seed + 2, "low": seed,
         "close": seed + 1, "volume": 100},
        {"date": "2026-01-02", "open": seed, "high": seed + 1, "low": seed,
         "close": seed + 1, "volume": 90},
    ]


def fixture(tmp_path: Path, *, offline=False):
    root = tmp_path / "source"
    root.mkdir()
    metadata = {}
    for index, symbol in enumerate(UNIVERSE):
        (root / f"{symbol}.csv").write_bytes(serialize_market_rows(rows(index + 1)))
        metadata[symbol] = {"provider": "AKShare/Eastmoney", "endpoint": "fund_etf_hist_em",
                            "frequency": "daily", "adjustment": "qfq",
                            "requested_start": "2005-01-01", "requested_end": "2026-07-17",
                            "acquisition_timestamp": "2026-07-20T00:00:00+00:00"}
    manifest = build_manifest(universe=UNIVERSE, csv_root=root, protocol_hash="a" * 64,
                              acquisition_config_hash="b" * 64, source_tree_fingerprint="c" * 64,
                              metadata=metadata, acquisition_attempt_id="synthetic-attempt")
    write_manifest(root / "manifest.json", manifest)
    archive = tmp_path / "snapshot.tar"
    build_archive(archive, root, UNIVERSE)
    spec = {"schema_version": PACKAGE_SCHEMA_VERSION, "executable": True,
            "snapshot_name": "synthetic", "release_tag": "synthetic-v1",
            "asset_name": "snapshot.tar", "immutable_url": archive.resolve().as_uri(),
            "archive_sha256": sha256_bytes(archive.read_bytes()),
            "manifest_sha256": sha256_bytes((root / "manifest.json").read_bytes()),
            "canonical_snapshot_identity": manifest["canonical_snapshot_identity"],
            "expected_files": [f"{x}.csv" for x in UNIVERSE] + ["manifest.json"],
            "archive_format": "tar", "cache": {"offline": offline,
                                                        "directory": str(tmp_path / "cache")}}
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return root, archive, spec, spec_path


def rewrite_tar(path: Path, members: list[tuple[str, bytes, str]]):
    with tarfile.open(path, "w") as archive:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if kind == "symlink":
                info.type, info.linkname, info.size = tarfile.SYMTYPE, "510300.csv", 0
            archive.addfile(info, io.BytesIO(data) if kind == "file" else None)


def resign_archive(spec, spec_path, archive):
    spec["archive_sha256"] = sha256_bytes(archive.read_bytes())
    spec_path.write_text(json.dumps(spec), encoding="utf-8")


def test_serializer_is_byte_deterministic_and_canonical():
    first = serialize_market_rows(rows())
    assert first == serialize_market_rows(reversed(rows()))
    assert first.startswith(b"date,open,high,low,close,volume\n2026-01-02,1.00000000")
    assert b"\r" not in first


@pytest.mark.parametrize("bad", [
    [{"date": "2026-01-01", "open": None, "high": 2, "low": 1, "close": 2, "volume": 1}],
    [{"date": "2026-01-01", "open": float("nan"), "high": 2, "low": 1, "close": 2, "volume": 1}],
    [*rows(), rows()[0]],
])
def test_serializer_rejects_missing_nonfinite_and_duplicate_dates(bad):
    with pytest.raises(ValueError):
        serialize_market_rows(bad)


def test_manifest_has_complete_v2_provenance(tmp_path):
    root, _, _, _ = fixture(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["all_symbol_success"] is True and manifest["no_fallback"] is True
    assert manifest["ordered_universe"] == list(UNIVERSE)
    entry = manifest["entries"][0]
    assert {"symbol", "relative_path", "sha256", "byte_size", "row_count", "first_date",
            "last_date", "provider", "endpoint", "frequency", "adjustment", "requested_start",
            "requested_end", "actual_start", "actual_end", "acquisition_timestamp",
            "acquisition_attempt_id", "serializer_version", "schema_version"} <= set(entry)


def test_deterministic_archive_rebuild(tmp_path):
    root, first, _, _ = fixture(tmp_path)
    second = tmp_path / "second.tar"
    assert build_archive(second, root, UNIVERSE) == sha256_bytes(first.read_bytes())
    assert second.read_bytes() == first.read_bytes()


def test_materialize_local_synthetic_package(tmp_path):
    _, _, _, spec_path = fixture(tmp_path)
    output = tmp_path / "output"
    materialize(spec_path, output)
    assert sorted(x.name for x in output.iterdir()) == sorted([f"{x}.csv" for x in UNIVERSE] + ["manifest.json"])


def test_package_template_is_explicitly_non_executable():
    with pytest.raises(ValueError, match="non-executable"):
        load_package_spec("configs/validation/etf9_snapshot_v2_package_template.json")


def test_unknown_package_field_rejected(tmp_path):
    _, _, spec, spec_path = fixture(tmp_path)
    spec["surprise"] = True
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown/missing"):
        materialize(spec_path, tmp_path / "output")


def test_archive_single_byte_mutation_and_wrong_sha_cleanup(tmp_path):
    _, archive, _, spec_path = fixture(tmp_path)
    data = bytearray(archive.read_bytes()); data[10] ^= 1; archive.write_bytes(data)
    with pytest.raises(ValueError, match="archive SHA"):
        materialize(spec_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".output.staging-*"))


@pytest.mark.parametrize("change", ["csv", "manifest", "identity", "provider", "mixed"])
def test_resigned_semantic_mutations_are_rejected(tmp_path, change):
    root, archive, spec, spec_path = fixture(tmp_path)
    manifest = json.loads((root / "manifest.json").read_text())
    if change == "csv":
        path = root / "510300.csv"; data = bytearray(path.read_bytes()); data[-2] ^= 1; path.write_bytes(data)
    elif change == "identity":
        spec["canonical_snapshot_identity"] = "d" * 64
    elif change == "provider":
        manifest["entries"][0]["provider"] = "fallback"
    elif change == "mixed":
        manifest["entries"][0]["adjustment"] = "hfq"
    else:
        manifest["source_tree_fingerprint"] = "e" * 64
    if change in {"manifest", "provider", "mixed"}:
        write_manifest(root / "manifest.json", manifest)
    if change in {"provider", "mixed"}:
        spec["manifest_sha256"] = sha256_bytes((root / "manifest.json").read_bytes())
    build_archive(archive, root, UNIVERSE)
    resign_archive(spec, spec_path, archive)
    with pytest.raises(ValueError):
        materialize(spec_path, tmp_path / "output")


@pytest.mark.parametrize("attack", ["missing", "extra", "renamed", "duplicate", "traversal", "absolute", "symlink"])
def test_hostile_archive_members_rejected(tmp_path, attack):
    root, archive, spec, spec_path = fixture(tmp_path)
    members = [(name, (root / name).read_bytes(), "file") for name in spec["expected_files"]]
    if attack == "missing": members.pop(0)
    elif attack == "extra": members.append(("extra.csv", b"x", "file"))
    elif attack == "renamed": members[0] = ("renamed.csv", members[0][1], "file")
    elif attack == "duplicate": members.append(members[0])
    elif attack == "traversal": members[0] = ("../escape", members[0][1], "file")
    elif attack == "absolute": members[0] = ("/escape", members[0][1], "file")
    elif attack == "symlink": members[0] = (members[0][0], b"", "symlink")
    rewrite_tar(archive, members)
    resign_archive(spec, spec_path, archive)
    with pytest.raises(ValueError):
        materialize(spec_path, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("damage", [b"not a tar", b"\0" * 100])
def test_corrupt_and_truncated_archives_rejected(tmp_path, damage):
    _, archive, spec, spec_path = fixture(tmp_path)
    archive.write_bytes(damage); resign_archive(spec, spec_path, archive)
    with pytest.raises(ValueError):
        materialize(spec_path, tmp_path / "output")


def test_verified_offline_cache(tmp_path):
    _, archive, spec, spec_path = fixture(tmp_path)
    cache = Path(spec["cache"]["directory"]); cache.mkdir()
    cached = cache / f'{spec["archive_sha256"]}.tar'; cached.write_bytes(archive.read_bytes())
    archive.unlink(); spec["cache"]["offline"] = True
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    materialize(spec_path, tmp_path / "output")
    assert (tmp_path / "output" / "manifest.json").is_file()


def test_offline_cache_miss_never_falls_back(tmp_path):
    _, _, spec, spec_path = fixture(tmp_path)
    spec["cache"]["offline"] = True
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="offline cache"):
        materialize(spec_path, tmp_path / "output")


def test_historical_validation_artifacts_are_byte_identical():
    expected = {
        "data/snapshots/sit-20260719/manifest.json": "682e83a62e8acc3d4ef3a45c32174a6eff2e668df101bb12e02104d847141643",
        "docs/validation/sit_snapshot_recovery.md": "4c1a96c117cf1ab99bda3a6631a0a757d20e5669d4681ad63a7c4eebbe5ef79c",
        "docs/validation/tsmom_real_market_report.md": "ffa0728e459ab76af315dd288ba2fda91e2766d48e094a68a5f91c8ecbd1e76c",
        "docs/validation/tsmom_real_market_judgment_inputs.json": "82c2288af997ba2360e18ccf5a1c82e782c87082d6e4c1da2cb5e9c79768675d",
        "configs/validation/sit_real_market_fixed.json": "0bc6c7974021ee62ad3abbdaef48c7d2236990210546a92b9b403e5738066aba",
        "configs/validation/sit_real_market_walk_forward.json": "297988f0a42335e1718113cdf36af85519041888ec8f9478fec93b4222f9a8a3",
        "configs/validation/sit_validation_policy.json": "a3ebc47943b38ef8d246a6691817e8fd58c6e72ecbf8140441b48590e54eb00e",
        "docs/validation/sit_real_market_protocol.md": "70b9e0c50dc7bae2699d1c083cf36bd06351c8f4c3e3b51b42a6dfd045c45b86",
        "configs/validation/tsmom_real_market_fixed.json": "d544ac428f3f81dd09f4b82728c865d76a5cc1cdb123afb81dbd86e0ae281781",
        "configs/validation/tsmom_real_market_walk_forward.json": "3bfe2bdac16e02e607fde262a91d512961be957f62a4f5268dc92c3cf944670e",
        "configs/validation/tsmom_validation_policy.json": "2fc1fa370fe1750195a66879211de1e40198db5b695442900cb286febdce9561",
        "docs/validation/tsmom_real_market_protocol.md": "90fb770c00a986ee31742472275f9fb8dbc695d9a489c2fdf6cd36ad4c7552cf",
    }
    for name, digest in expected.items():
        assert sha256_bytes(Path(name).read_bytes()) == digest
