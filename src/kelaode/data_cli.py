"""Command line tools for reproducible ETF daily datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .market_data import AKShareETFDownloader, MarketDataset, read_daily_bars


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m kelaode.data_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download")
    download.add_argument("--symbols", required=True, help="comma-separated six-digit symbols")
    download.add_argument("--start", required=True)
    download.add_argument("--end", required=True)
    download.add_argument("--output", required=True, type=Path)
    download.add_argument("--adjust", choices=("", "qfq", "hfq"), default="qfq")
    download.add_argument("--retries", type=int, default=2)
    download.add_argument("--format", choices=("parquet", "csv"), default="parquet")
    validate = commands.add_parser("validate")
    validate.add_argument("--input", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "download":
        manifest = AKShareETFDownloader().download_many(
            [item.strip() for item in args.symbols.split(",") if item.strip()],
            args.start, args.end, args.output, args.adjust, args.retries, args.format,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 1 if any(item["status"] == "error" for item in manifest["entries"]) else 0

    files = sorted(args.input.glob("*.parquet")) + sorted(args.input.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no .parquet or .csv data files in {args.input}")
    dataset = MarketDataset({path.stem: read_daily_bars(path) for path in files})
    quality = dataset.quality()
    report = {"symbols": dataset.symbols, "missing_ratio": quality.missing_ratio,
              "common_start": quality.common_start.isoformat() if quality.common_start else None,
              "common_end": quality.common_end.isoformat() if quality.common_end else None,
              "warnings": quality.warnings}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
