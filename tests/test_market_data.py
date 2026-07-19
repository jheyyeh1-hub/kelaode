from datetime import date

import pytest

from kelaode.market_data import AKShareETFDownloader, read_daily_bars


class FakeFrame:
    columns = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]

    def to_dict(self, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return [
            {"日期": "2024-01-03", "开盘": 4.1, "最高": 4.3, "最低": 4.0, "收盘": 4.2, "成交量": 20},
            {"日期": "2024-01-02", "开盘": 4.0, "最高": 4.2, "最低": 3.9, "收盘": 4.1, "成交量": 10},
        ]


def test_akshare_downloader_normalizes_and_persists(tmp_path) -> None:
    calls: list[dict[str, str]] = []

    def fake_client(**kwargs: str) -> FakeFrame:
        calls.append(kwargs)
        return FakeFrame()

    output = tmp_path / "510300.csv"
    bars = AKShareETFDownloader(fake_client).download_csv(
        "510300", date(2024, 1, 1), "20240103", output
    )

    assert calls == [
        {
            "symbol": "510300",
            "period": "daily",
            "start_date": "20240101",
            "end_date": "20240103",
            "adjust": "qfq",
        }
    ]
    assert [bar.trade_date for bar in bars] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert read_daily_bars(output) == bars


def test_akshare_downloader_rejects_non_six_digit_symbol() -> None:
    with pytest.raises(ValueError, match="six digits"):
        AKShareETFDownloader(lambda **_: FakeFrame()).fetch("510300.SH", "20240101", "20240103")
