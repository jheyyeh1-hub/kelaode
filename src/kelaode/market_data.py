"""AKShare-backed daily market data for mainland China ETFs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable


@dataclass(frozen=True, order=True)
class DailyBar:
    """One adjusted daily OHLCV bar."""

    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")


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
            bars[bar.trade_date] = bar
        return sorted(bars.values(), key=lambda item: item.trade_date)

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

    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows: Iterable[dict[str, str]] = csv.DictReader(handle)
        return [
            DailyBar(
                trade_date=AKShareETFDownloader._parse_date(row["date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in rows
        ]
