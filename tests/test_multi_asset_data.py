from datetime import date
import json

import pytest

from kelaode.market_data import DailyBar, MarketDataset, AKShareETFDownloader, read_daily_bars, write_daily_bars


def bar(day: int, close: float = 10) -> DailyBar:
    return DailyBar(date(2024, 1, day), close, close + 1, close - 1, close, 100)


class Frame:
    columns = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
    def to_dict(self, orient):
        return [{"日期": "2024-01-02", "开盘": 10, "收盘": 10, "最高": 11, "最低": 9, "成交量": 1}]


def test_alignment_and_missing_statistics():
    data = MarketDataset({"a": [bar(3), bar(2)], "b": [bar(3), bar(4)]})
    assert data.all_dates == (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4))
    assert data.common_dates == (date(2024, 1, 3),)
    assert data.aligned("union")[date(2024, 1, 2)]["b"] is None
    assert list(data.aligned("intersection")) == [date(2024, 1, 3)]
    assert data.quality().missing_ratio == {"a": pytest.approx(1 / 3), "b": pytest.approx(1 / 3)}


def test_dataset_rejects_duplicate_and_empty_symbol():
    with pytest.raises(ValueError, match="duplicate"):
        MarketDataset({"a": [bar(2), bar(2)]})
    with pytest.raises(ValueError, match="empty"):
        MarketDataset({" ": [bar(2)]})


def test_invalid_ohlc_rejected():
    with pytest.raises(ValueError, match="range"):
        DailyBar(date(2024, 1, 2), 10, 9, 8, 10, 1)


def test_csv_roundtrip_and_missing_file(tmp_path):
    path = tmp_path / "bars.csv"; expected = [bar(2), bar(3)]
    write_daily_bars(path, expected)
    assert read_daily_bars(path) == expected
    with pytest.raises(FileNotFoundError):
        read_daily_bars(tmp_path / "absent.csv")


def test_batch_success_failure_retry_and_manifest(tmp_path):
    attempts = {"510300": 0, "510500": 0, "159915": 0}
    def client(**kwargs):
        symbol = kwargs["symbol"]; attempts[symbol] += 1
        if symbol == "510500":
            raise ConnectionError("offline")
        if symbol == "159915" and attempts[symbol] == 1:
            raise TimeoutError("retry")
        return Frame()
    result = AKShareETFDownloader(client).download_many(
        attempts, "2024-01-01", "2024-01-03", tmp_path, retries=1, file_format="csv")
    assert [entry["status"] for entry in result["entries"]] == ["success", "error", "success"]
    assert attempts == {"510300": 1, "510500": 2, "159915": 2}
    assert result["entries"][0]["row_count"] == 1
    assert "ConnectionError" in result["entries"][1]["error"]
    assert json.loads((tmp_path / "manifest.json").read_text()) == result


def test_parquet_roundtrip_when_dependency_available(tmp_path):
    pytest.importorskip("pyarrow")
    path = tmp_path / "bars.parquet"; expected = [bar(2)]
    write_daily_bars(path, expected)
    assert read_daily_bars(path) == expected
