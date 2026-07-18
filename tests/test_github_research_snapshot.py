import json
from pathlib import Path


def test_github_snapshot_contains_core_project_categories() -> None:
    projects = json.loads(Path("data/github_quant_projects_2026-07-18.json").read_text())
    repos = {project["repo"] for project in projects}

    assert "vnpy/vnpy" in repos
    assert "ricequant/rqalpha" in repos
    assert "1nchaos/adata" in repos
    assert "ai4trade/XtQuant" in repos


def test_github_report_mentions_performance_caveat() -> None:
    report = Path("docs/github_a_share_quant_landscape.md").read_text()

    assert "不是经过第三方审计的实盘基金产品" in report
    assert "最终战绩" in report
    assert "QMT" in report
