from __future__ import annotations

import hashlib
import json
import shutil
import socket
from dataclasses import replace
from pathlib import Path

import pytest

from kelaode.experiment import ExperimentConfig
from kelaode.market_data import AKShareETFDownloader
from kelaode.selection_runner import run_fixed_selection, run_walk_forward
from kelaode.snapshot import SnapshotManifest, sha256_file
from kelaode.validation_audit import audit_selection


def _policy(path: Path) -> Path:
    path.write_text(json.dumps({"official_listing_dates": {
        "STABLE": "2024-01-24", "VOLATILE": "2024-01-24", "LATE": "2024-01-31"}}))
    return path


def _config(tmp_path: Path, mode="fixed_selection") -> ExperimentConfig:
    name = "configs/sit_synthetic_fixed.json" if mode == "fixed_selection" else "configs/sit_synthetic_walk_forward.json"
    config = ExperimentConfig.from_json(name)
    scenarios = {"base": {"commission_rate": .001, "minimum_commission": 2, "slippage_rate": .001},
                 "moderate": {"commission_rate": .002, "minimum_commission": 3, "slippage_rate": .002}}
    return replace(config, output_directory=str(tmp_path / "results"),
                   cost_analysis={"closed_loop": scenarios, "fixed_path": scenarios})


def _reseal(root: Path) -> None:
    manifests = sorted(root.glob("**/artifact_manifest.json"), key=lambda p: len(p.parts), reverse=True)
    for path in manifests:
        value = json.loads(path.read_text())
        directory = path.parent
        value["artifacts"] = {name: sha256_file(directory / name) for name in value.get("artifacts", {})}
        if "children" in value:
            value["children"] = {relative: sha256_file(directory / relative / "artifact_manifest.json")
                                 for relative in value["children"]}
        path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def _mutate_json(path: Path, callback) -> None:
    value = json.loads(path.read_text()); callback(value)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def test_data_freeze_provider_failure_has_no_fallback_or_valid_snapshot(tmp_path):
    calls = []
    def primary(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("primary unavailable")
    root = tmp_path / "freeze"
    manifest = AKShareETFDownloader(primary).download_many(["510300"], "2024-01-01", "2024-01-31", root,
                                                            adjust="qfq", retries=2, file_format="csv")
    assert len(calls) == 3
    assert manifest["entries"][0]["status"] == "error"
    assert list(root.glob("*.csv")) == []
    with pytest.raises(ValueError, match="failed download"):
        SnapshotManifest.load(root / "manifest.json")


def test_mixed_adjustment_and_one_byte_snapshot_mutation_are_rejected(tmp_path):
    source = Path("tests/fixtures/snapshot")
    root = tmp_path / "snapshot"; shutil.copytree(source, root)
    raw = json.loads((root / "manifest.json").read_text())
    raw["entries"][1]["adjustment"] = "hfq"
    (root / "manifest.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="mixed adjustment"):
        SnapshotManifest.load(root / "manifest.json").validate(root)
    raw["entries"][1]["adjustment"] = raw["entries"][0]["adjustment"]
    (root / "manifest.json").write_text(json.dumps(raw))
    data = root / raw["entries"][0]["relative_path"]
    data.write_bytes(data.read_bytes() + b"x")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        SnapshotManifest.load(root / "manifest.json").validate(root)


def test_selection_runners_never_open_network(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    run_fixed_selection(replace(ExperimentConfig.from_json("configs/sit_synthetic_fixed.json"),
                                output_directory=str(tmp_path / "fixed")))
    run_walk_forward(replace(ExperimentConfig.from_json("configs/sit_synthetic_walk_forward.json"),
                             output_directory=str(tmp_path / "walk")))


@pytest.fixture
def sealed_fixed(tmp_path):
    policy = _policy(tmp_path / "policy.json")
    root = run_fixed_selection(_config(tmp_path))
    assert audit_selection(root, policy)["status"] == "pass"
    return root, policy


@pytest.mark.parametrize("kind", ["parent", "candidate", "test"])
def test_identity_mutation_is_detected(sealed_fixed, kind):
    root, policy = sealed_fixed
    result = json.loads((root / "result.json").read_text())
    if kind == "parent": path = root / "identity.json"
    elif kind == "candidate": path = root / "candidates" / result["candidate_table"][0]["candidate_id"] / "identity.json"
    else: path = root / result["frozen_test_bundle"] / "identity.json"
    _mutate_json(path, lambda value: value["canonical_inputs"].update({"tampered": True}))
    _reseal(root)
    with pytest.raises(ValueError, match="identity"):
        audit_selection(root, policy)


def test_extra_unselected_and_missing_selected_test_are_detected(sealed_fixed):
    root, policy = sealed_fixed
    result = json.loads((root / "result.json").read_text())
    candidate = root / "candidates" / result["candidate_table"][0]["candidate_id"]
    extra = candidate / "frozen_test" / "extra"
    shutil.copytree(root / result["frozen_test_bundle"], extra)
    manifest = json.loads((candidate / "artifact_manifest.json").read_text())
    manifest["children"]["frozen_test/extra"] = sha256_file(extra / "artifact_manifest.json")
    (candidate / "artifact_manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    _reseal(root)
    with pytest.raises(ValueError, match="unselected candidate"):
        audit_selection(root, policy)


def test_missing_selected_test_is_detected(sealed_fixed):
    root, policy = sealed_fixed
    result = json.loads((root / "result.json").read_text())
    selected = root / result["frozen_test_bundle"]
    shutil.rmtree(selected)
    selected.parent.rmdir()
    manifest = json.loads((root / "artifact_manifest.json").read_text())
    manifest["children"].pop(result["frozen_test_bundle"])
    manifest["expected_counts"]["frozen_tests"] = 0
    (root / "artifact_manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    _reseal(root)
    with pytest.raises(ValueError, match="exactly one"):
        audit_selection(root, policy)


@pytest.mark.parametrize("mutation,match", [
    (lambda r: r["test_metrics"].update({"turnover": r["test_metrics"]["turnover"] + 1}), "turnover"),
    (lambda r: r["cost_analysis"]["fixed_path_contract"].update({"base_fill_path_sha256": "0" * 64}), "fill SHA"),
    (lambda r: r["cost_analysis"]["fixed_path_contract"].update({"base_replay_final_equity": 1}), "base replay"),
])
def test_turnover_and_fixed_path_mutations_are_detected(sealed_fixed, mutation, match):
    root, policy = sealed_fixed
    _mutate_json(root / "result.json", mutation); _reseal(root)
    with pytest.raises(ValueError, match=match):
        audit_selection(root, policy)


def test_official_prelisting_position_and_benchmark_mismatch_are_detected(sealed_fixed):
    root, policy = sealed_fixed
    bad_policy = policy.with_name("future-policy.json")
    metadata = json.loads(policy.read_text())
    metadata["official_listing_dates"] = {symbol: "2099-01-01" for symbol in metadata["official_listing_dates"]}
    bad_policy.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="pre-listing"):
        audit_selection(root, bad_policy)


def test_benchmark_date_mismatch_is_detected(sealed_fixed):
    root, policy = sealed_fixed
    result = json.loads((root / "result.json").read_text())
    curve = root / result["frozen_test_bundle"] / "benchmark_curve.csv"
    lines = curve.read_text().splitlines(); curve.write_text("\n".join(lines[:-1]) + "\n")
    _reseal(root)
    with pytest.raises(ValueError, match="benchmark dates"):
        audit_selection(root, policy)


def test_fold_identity_and_stitched_value_mutations_are_detected(tmp_path):
    policy = _policy(tmp_path / "policy.json")
    root = run_walk_forward(_config(tmp_path, "walk_forward"))
    result = json.loads((root / "result.json").read_text())
    fold_identity = root / "fold-0000" / "identity.json"
    _mutate_json(fold_identity, lambda v: v["canonical_inputs"].update({"tampered": True})); _reseal(root)
    with pytest.raises(ValueError, match="fold identity"):
        audit_selection(root, policy)


def test_stitched_value_mismatch_with_correct_dates_is_detected(tmp_path):
    policy = _policy(tmp_path / "policy.json")
    root = run_walk_forward(_config(tmp_path, "walk_forward"))
    curve = root / "stitched_oos_equity.csv"
    lines = curve.read_text().splitlines(); fields = lines[1].split(","); fields[1] = str(float(fields[1]) + 1)
    lines[1] = ",".join(fields); curve.write_text("\n".join(lines) + "\n"); _reseal(root)
    with pytest.raises(ValueError, match="stitched OOS equity"):
        audit_selection(root, policy)
