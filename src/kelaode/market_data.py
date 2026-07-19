"""AKShare-backed daily market data for mainland China ETFs."""

from __future__ import annotations

import csv
import hashlib
import json
import time
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

SCHEMA_VERSION = "1.0"
DEFAULT_ETF_UNIVERSE: tuple[str, ...] = (
    "510300", "510500", "159915", "512100", "512880",
    "512690", "512480", "518880", "513100", "511010",
)


@dataclass(frozen=True, order=True)
class DailyBar:
    """One adjusted daily OHLCV bar."""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    previous_close: float | None = None
    upper_limit: float | None = None
    lower_limit: float | None = None
    suspended: bool | None = None

    def __post_init__(self) -> None:
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        for name, value in (("previous_close", self.previous_close),
                            ("upper_limit", self.upper_limit), ("lower_limit", self.lower_limit)):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")


@dataclass(frozen=True)
class DatasetQuality:
    missing_ratio: Mapping[str, float]
    common_start: date | None
    common_end: date | None
    warnings: tuple[str, ...] = ()


class MarketDataset:
    """Immutable, deterministic multi-symbol daily-bar collection."""

    def __init__(self, data: Mapping[str, Iterable[DailyBar]]) -> None:
        normalized: dict[str, tuple[DailyBar, ...]] = {}
        for raw_symbol, values in data.items():
            symbol = raw_symbol.strip()
            if not symbol:
                raise ValueError("symbol cannot be empty")
            bars = tuple(sorted(values, key=lambda bar: bar.trade_date))
            dates = [bar.trade_date for bar in bars]
            if len(dates) != len(set(dates)):
                raise ValueError(f"duplicate dates for symbol {symbol}")
            normalized[symbol] = bars
        self._data = normalized

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._data)

    def history(self, symbol: str) -> tuple[DailyBar, ...]:
        return self._data[symbol]

    def on_date(self, trade_date: date) -> dict[str, DailyBar]:
        return {symbol: bar for symbol, bars in self._data.items()
                for bar in bars if bar.trade_date == trade_date}

    def has_bar(self, symbol: str, trade_date: date) -> bool:
        return any(bar.trade_date == trade_date for bar in self.history(symbol))

    def trading_dates(self, mode: Literal["union", "intersection"] = "union",
                      symbols: Iterable[str] | None = None) -> tuple[date, ...]:
        selected = tuple(symbols) if symbols is not None else self.symbols
        if not selected:
            return ()
        sets = [{bar.trade_date for bar in self.history(symbol)} for symbol in selected]
        dates = set.union(*sets) if mode == "union" else set.intersection(*sets) if mode == "intersection" else None
        if dates is None:
            raise ValueError("mode must be 'union' or 'intersection'")
        return tuple(sorted(dates))

    @property
    def all_dates(self) -> tuple[date, ...]:
        return self.trading_dates("union")

    @property
    def common_dates(self) -> tuple[date, ...]:
        return self.trading_dates("intersection")

    def aligned(self, mode: Literal["union", "intersection"] = "union") -> dict[date, dict[str, DailyBar | None]]:
        return {day: {symbol: self.on_date(day).get(symbol) for symbol in self.symbols}
                for day in self.trading_dates(mode)}

    def quality(self, jump_threshold: float = 0.25) -> DatasetQuality:
        union = self.all_dates
        messages: list[str] = []
        missing = {s: (1 - len(self.history(s)) / len(union) if union else 0.0) for s in self.symbols}
        starts, ends = [], []
        for symbol in self.symbols:
            bars = self.history(symbol)
            if not bars:
                messages.append(f"{symbol}: data is empty")
                continue
            starts.append(bars[0].trade_date); ends.append(bars[-1].trade_date)
            for previous, current in zip(bars, bars[1:]):
                change = abs(current.close / previous.close - 1)
                if change > jump_threshold:
                    messages.append(f"{symbol}: close changed {change:.1%} on {current.trade_date}")
        return DatasetQuality(missing, max(starts) if starts else None,
                              min(ends) if ends else None, tuple(messages))


class AKShareETFDownloader:
    """Download and normalize Eastmoney ETF history through AKShare.

    The client can be injected so unit tests and offline research never access the
    network.  AKShare is imported lazily to keep existing core users lightweight.
    """

    def __init__(self, history_client: Callable[..., Any] | None = None) -> None:
        self._history_client = history_client

    def fetch(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
        adjust: str = "qfq",
    ) -> list[DailyBar]:
        if adjust not in {"", "qfq", "hfq"}:
            raise ValueError("adjust must be '', 'qfq', or 'hfq'")
        if not symbol.isdigit() or len(symbol) != 6:
            raise ValueError("AKShare ETF symbol must contain exactly six digits")

        client = self._history_client or self._load_client()
        frame = client(
            symbol=symbol,
            period="daily",
            start_date=self._compact_date(start_date),
            end_date=self._compact_date(end_date),
            adjust=adjust,
        )
        required = {"日期", "开盘", "收盘", "最高", "最低", "成交量"}
        if frame is None or not required.issubset(frame.columns):
            missing = required - set(getattr(frame, "columns", []))
            raise ValueError(f"AKShare response is missing columns: {sorted(missing)}")

        bars: dict[date, DailyBar] = {}
        for row in frame.to_dict("records"):
            try:
                bar = DailyBar(
                    trade_date=self._parse_date(row["日期"]),
                    open=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=float(row["成交量"]),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid AKShare row: {row!r}") from exc
            if bar.trade_date in bars:
                raise ValueError(f"duplicate date in AKShare response: {bar.trade_date}")
            bars[bar.trade_date] = bar
        return sorted(bars.values(), key=lambda item: item.trade_date)

    def download_many(self, symbols: Iterable[str], start_date: date | str,
                      end_date: date | str, output_dir: str | Path,
                      adjust: str = "qfq", retries: int = 2,
                      file_format: Literal["csv", "parquet"] = "parquet") -> dict[str, Any]:
        """Download each symbol independently and always write an auditable manifest."""
        if retries < 0:
            raise ValueError("retries cannot be negative")
        requested_start, requested_end = self._parse_date(start_date), self._parse_date(end_date)
        root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, Any]] = []
        for symbol in symbols:
            entry: dict[str, Any] = {"symbol": symbol, "provider": "AKShare/Eastmoney",
                "endpoint": "fund_etf_hist_em", "adjustment": adjust or "unadjusted",
                "requested_start": requested_start.isoformat(),
                "requested_end": requested_end.isoformat(), "schema_version": SCHEMA_VERSION,
                "downloaded_at": datetime.now(timezone.utc).isoformat()}
            error: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    bars = self.fetch(symbol, requested_start, requested_end, adjust)
                    if not bars:
                        raise ValueError("downloaded data is empty")
                    validate_bars(bars, requested_start, requested_end)
                    path = root / f"{symbol}.{file_format}"
                    write_daily_bars(path, bars)
                    entry.update(row_count=len(bars), actual_start=bars[0].trade_date.isoformat(),
                                 actual_end=bars[-1].trade_date.isoformat(), file_format=file_format,
                                 relative_path=path.name,
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 status="success", error=None)
                    break
                except Exception as exc:  # independent failures belong in the manifest
                    error = exc
                    if attempt < retries:
                        time.sleep(min(0.1 * (2 ** attempt), 1.0))
            else:
                entry.update(status="error", error=f"{type(error).__name__}: {error}",
                             row_count=0, actual_start=None, actual_end=None,
                             file_format=file_format, relative_path=None, sha256=None)
            entries.append(entry)
        manifest = {"schema_version": SCHEMA_VERSION, "entries": entries}
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    def download_csv(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
        output: str | Path,
        adjust: str = "qfq",
    ) -> list[DailyBar]:
        """Fetch bars and persist the normalized dataset as UTF-8 CSV."""

        bars = self.fetch(symbol, start_date, end_date, adjust)
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "open", "high", "low", "close", "volume"])
            for bar in bars:
                writer.writerow([bar.trade_date.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume])
        return bars

    @staticmethod
    def _load_client() -> Callable[..., Any]:
        try:
            import akshare  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("install the 'data' extra to download ETF data") from exc
        return akshare.fund_etf_hist_em

    @staticmethod
    def _compact_date(value: date | str) -> str:
        parsed = AKShareETFDownloader._parse_date(value)
        return parsed.strftime("%Y%m%d")

    @staticmethod
    def _parse_date(value: date | str | datetime) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        for pattern in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                pass
        raise ValueError(f"unsupported date: {value!r}")


def read_daily_bars(path: str | Path) -> list[DailyBar]:
    """Read normalized bars written by :meth:`download_csv`."""

    source = Path(path)
    if source.suffix == ".parquet":
        try:
            import pandas as pd  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("install the 'parquet' extra to read Parquet") from exc
        records = pd.read_parquet(source).to_dict("records")
        bars = [_bar_from_record(row) for row in records]
        validate_bars(bars)
        return bars
    with source.open(encoding="utf-8", newline="") as handle:
        rows: Iterable[dict[str, str]] = csv.DictReader(handle)
        bars = [_bar_from_record(row) for row in rows]
    validate_bars(bars)
    return bars


def _bar_from_record(row: Mapping[str, Any]) -> DailyBar:
    return DailyBar(AKShareETFDownloader._parse_date(row["date"]), float(row["open"]),
                    float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"]))


def write_daily_bars(path: str | Path, bars: Sequence[DailyBar]) -> None:
    validate_bars(bars)
    target = Path(path); target.parent.mkdir(parents=True, exist_ok=True)
    records = [{"date": b.trade_date.isoformat(), "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "volume": b.volume} for b in bars]
    if target.suffix == ".parquet":
        try:
            import pandas as pd  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("install the 'parquet' extra to write Parquet") from exc
        pd.DataFrame(records).to_parquet(target, index=False)
    elif target.suffix == ".csv":
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", "open", "high", "low", "close", "volume"])
            writer.writeheader(); writer.writerows(records)
    else:
        raise ValueError("file extension must be .csv or .parquet")


def validate_bars(bars: Sequence[DailyBar], requested_start: date | None = None,
                  requested_end: date | None = None) -> tuple[str, ...]:
    if not bars:
        raise ValueError("data is empty")
    dates = [bar.trade_date for bar in bars]
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate dates")
    if dates != sorted(dates):
        raise ValueError("dates must be increasing")
    if requested_start and dates[0] < requested_start:
        raise ValueError("data starts before requested range")
    if requested_end and dates[-1] > requested_end:
        raise ValueError("data ends after requested range")
    messages = []
    for previous, current in zip(bars, bars[1:]):
        change = abs(current.close / previous.close - 1)
        if change > .25:
            messages.append(f"close changed {change:.1%} on {current.trade_date}")
    for message in messages:
        warnings.warn(message, RuntimeWarning, stacklevel=2)
    return tuple(messages)
