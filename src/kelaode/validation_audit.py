"""Read-only independent checks for sealed schema-2.0 selection artifacts."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .selection_runner import validate_artifact_directory
from .snapshot import SnapshotManifest


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _audit_run(bundle: Path, listing: dict[str, str]) -> list[str]:
    checks: list[str] = []
    # The shared-runner manifest is independently checked by the recursive parent validator.
    equity = {r["date"]: float(r["equity"]) for r in _rows(bundle / "equity_curve.csv")}
    cash = {r["date"]: float(r["cash"]) for r in _rows(bundle / "cash.csv")}
    positions = {(r["date"], r["symbol"]): int(r["quantity"]) for r in _rows(bundle / "positions.csv")}
    marks = {(r["date"], r["symbol"]): (float(r["close"]) if r["close"] else None)
             for r in _rows(bundle / "marks.csv")}
    for day, value in equity.items():
        reconstructed = cash[day] + sum(q * (marks[(d, s)] or 0.0)
                                        for (d, s), q in positions.items() if d == day)
        if abs(reconstructed - value) > 1e-6:
            raise ValueError(f"equity accounting mismatch in {bundle} on {day}")
    checks.append("equity_equals_cash_plus_marked_positions")
    fills = [(r["date"], r["symbol"], r["side"], r["quantity"], r["price"], r["commission"])
             for r in _rows(bundle / "fills.csv")]
    trades = [(r["date"], r["symbol"], r["side"], r["quantity"], r["price"], r["commission"])
              for r in _rows(bundle / "trades.csv")]
    if fills != trades:
        raise ValueError(f"trades do not reconcile with fills: {bundle}")
    checks.append("trades_reconcile_with_fills")
    config = json.loads((bundle / "configuration.json").read_text())
    execution_start = config["execution_start_date"]
    orders = _rows(bundle / "orders.csv")
    if any(row["date"] < execution_start or row["signal_date"] >= row["date"] for row in orders):
        raise ValueError(f"warm-up or non-next-open order detected: {bundle}")
    checks.extend(("no_warmup_orders", "signal_precedes_execution"))
    for (day, symbol), quantity in positions.items():
        if quantity and day < listing[symbol]:
            raise ValueError(f"pre-listing position detected: {symbol} {day}")
    checks.append("no_prelisting_positions")
    strategy_dates = list(equity)
    benchmark_dates = [r["date"] for r in _rows(bundle / "benchmark_curve.csv")]
    if benchmark_dates and benchmark_dates != strategy_dates:
        raise ValueError(f"benchmark dates are not aligned: {bundle}")
    checks.append("benchmark_alignment")
    return checks


def audit_selection(root: str | Path) -> dict:
    """Audit sealed artifacts without importing a strategy or rerunning selection."""
    root = Path(root)
    validate_artifact_directory(root)
    config = json.loads((root / "configuration.json").read_text())
    manifest = SnapshotManifest.load(config["data_manifest"])
    manifest.validate(config["data_root"], expected_symbols=config["universe"],
                      allow_mixed_adjustments=False)
    listing = {entry.symbol: entry.actual_start for entry in manifest.entries}
    result = json.loads((root / "result.json").read_text())
    checks = ["artifact_hashes", "data_hashes", "consistent_adjustment"]
    bundles: list[Path] = []
    if result["mode"] == "fixed_selection":
        table_ids = {row["candidate_id"] for row in result["candidate_table"]}
        if result["selected_candidate_id"] not in table_ids:
            raise ValueError("selected candidate is absent from candidate table")
        bundles.append(root / result["frozen_test_bundle"])
        checks.extend(("selected_candidate_membership", "selected_only_frozen_test"))
    else:
        seen: set[str] = set()
        previous = None
        for fold in result["folds"]:
            ids = {row["candidate_id"] for row in fold["candidate_table"]}
            if fold["selected_candidate_id"] not in ids:
                raise ValueError("fold selection is absent from candidate table")
            dates = fold["boundaries"]["test"]
            if seen.intersection(dates) or (previous is not None and dates[0] <= previous):
                raise ValueError("overlapping or unordered OOS dates")
            seen.update(dates); previous = dates[-1]
            bundles.append(root / f"fold-{fold['fold']:04d}" / fold["frozen_test_bundle"])
        stitched = _rows(root / "stitched_oos_equity.csv")
        if [r["date"] for r in stitched] != sorted(seen):
            raise ValueError("stitched OOS dates do not equal fold OOS dates")
        checks.extend(("selected_candidate_membership", "no_oos_overlap", "stitched_oos_continuity"))
    for bundle in bundles:
        checks.extend(_audit_run(bundle, listing))
    return {"status": "pass", "mode": result["mode"], "bundle_count": len(bundles),
            "checks": sorted(set(checks))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kelaode.validation_audit")
    parser.add_argument("--artifacts", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(audit_selection(args.artifacts), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
