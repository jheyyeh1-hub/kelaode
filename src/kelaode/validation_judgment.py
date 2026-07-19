"""Pure mechanical evaluation of the preregistered SIT decision rules."""
from __future__ import annotations

from typing import Any, Mapping

JUDGMENTS = {"PASS", "CONDITIONAL", "FAIL", "NOT_EVALUATED"}


def evaluate_tsmom_judgment(metrics: Mapping[str, Any] | None,
                            thresholds: Mapping[str, float]) -> str:
    """Apply the preregistered TSMOM precedence without files or discretion."""
    if metrics is None or metrics.get("evaluated") is not True:
        return "NOT_EVALUATED"
    required = {
        "audits_pass", "accounting_reconstructable", "frozen_test_return",
        "frozen_test_excess_return", "stitched_oos_return", "stitched_max_drawdown",
        "moderate_equity_ratio", "severe_equity_ratio", "post_result_protocol_change",
    }
    missing = required - set(metrics)
    if missing:
        raise ValueError(f"judgment metrics missing: {sorted(missing)}")
    threshold_names = {
        "pass_frozen_test_return_gt", "pass_frozen_test_excess_return_gt",
        "pass_stitched_oos_return_gt", "pass_stitched_max_drawdown_ge",
        "pass_moderate_equity_ratio_ge", "fail_frozen_test_return_le",
        "fail_stitched_oos_return_le", "fail_stitched_max_drawdown_lt",
        "fail_moderate_equity_ratio_lt", "conditional_contribution_share_gt",
        "conditional_severe_equity_ratio_lt",
    }
    absent = threshold_names - set(thresholds)
    if absent:
        raise ValueError(f"judgment thresholds missing: {sorted(absent)}")
    if (not bool(metrics["audits_pass"])
            or not bool(metrics["accounting_reconstructable"])
            or bool(metrics["post_result_protocol_change"])
            or metrics["frozen_test_return"] <= thresholds["fail_frozen_test_return_le"]
            or metrics["stitched_oos_return"] <= thresholds["fail_stitched_oos_return_le"]
            or metrics["stitched_max_drawdown"] < thresholds["fail_stitched_max_drawdown_lt"]
            or metrics["moderate_equity_ratio"] < thresholds["fail_moderate_equity_ratio_lt"]):
        return "FAIL"
    if metrics.get("diagnostic_status", "COMPLETE") == "INCOMPLETE":
        return "CONDITIONAL"
    if metrics.get("diagnostic_status", "COMPLETE") != "COMPLETE":
        raise ValueError("diagnostic_status must be COMPLETE or INCOMPLETE")
    diagnostic_required = {
        "max_positive_contribution_share", "neighbor_sign_reversal", "parameter_instability"
    }
    missing = diagnostic_required - set(metrics)
    if missing:
        raise ValueError(f"judgment metrics missing: {sorted(missing)}")
    passes = (
        metrics["frozen_test_return"] > thresholds["pass_frozen_test_return_gt"]
        and metrics["frozen_test_excess_return"] > thresholds["pass_frozen_test_excess_return_gt"]
        and metrics["stitched_oos_return"] > thresholds["pass_stitched_oos_return_gt"]
        and metrics["stitched_max_drawdown"] >= thresholds["pass_stitched_max_drawdown_ge"]
        and metrics["moderate_equity_ratio"] >= thresholds["pass_moderate_equity_ratio_ge"]
    )
    conditional = (
        metrics["severe_equity_ratio"] < thresholds["conditional_severe_equity_ratio_lt"]
        or metrics["max_positive_contribution_share"] > thresholds["conditional_contribution_share_gt"]
        or bool(metrics["neighbor_sign_reversal"])
        or bool(metrics["parameter_instability"])
    )
    return "PASS" if passes and not conditional else "CONDITIONAL"


def evaluate_strategy_judgment(metrics: Mapping[str, Any] | None,
                               thresholds: Mapping[str, float]) -> str:
    """Return the frozen strategy judgment without reading files or running research.

    ``None`` or ``evaluated=False`` means that no economic experiment exists. FAIL
    precedence applies before CONDITIONAL and PASS once evaluation has occurred.
    """
    if metrics is None or metrics.get("evaluated") is not True:
        return "NOT_EVALUATED"
    required = {
        "audits_pass", "frozen_test_return", "frozen_test_excess_return",
        "stitched_oos_return", "stitched_max_drawdown", "moderate_equity_ratio",
        "severe_equity_ratio", "post_result_protocol_change",
    }
    missing = required - set(metrics)
    if missing:
        raise ValueError(f"judgment metrics missing: {sorted(missing)}")
    t_required = {
        "pass_frozen_test_excess_return_gt", "pass_stitched_oos_return_gt",
        "pass_stitched_max_drawdown_ge", "pass_moderate_equity_ratio_ge",
        "fail_frozen_test_return_le", "fail_stitched_oos_return_le",
        "fail_stitched_max_drawdown_lt", "fail_moderate_equity_ratio_lt",
        "conditional_contribution_share_gt", "conditional_severe_equity_ratio_lt",
    }
    absent = t_required - set(thresholds)
    if absent:
        raise ValueError(f"judgment thresholds missing: {sorted(absent)}")
    fail = (
        not bool(metrics["audits_pass"])
        or bool(metrics["post_result_protocol_change"])
        or metrics["frozen_test_return"] <= thresholds["fail_frozen_test_return_le"]
        or metrics["stitched_oos_return"] <= thresholds["fail_stitched_oos_return_le"]
        or metrics["stitched_max_drawdown"] < thresholds["fail_stitched_max_drawdown_lt"]
        or metrics["moderate_equity_ratio"] < thresholds["fail_moderate_equity_ratio_lt"]
    )
    if fail:
        return "FAIL"
    diagnostic_status = metrics.get("diagnostic_status", "COMPLETE")
    if diagnostic_status == "INCOMPLETE":
        return "CONDITIONAL"
    if diagnostic_status != "COMPLETE":
        raise ValueError("diagnostic_status must be COMPLETE or INCOMPLETE")
    diagnostic_required = {"max_positive_contribution_share", "neighbor_sign_reversal"}
    diagnostic_missing = diagnostic_required - set(metrics)
    if diagnostic_missing:
        raise ValueError(f"judgment metrics missing: {sorted(diagnostic_missing)}")
    conditional = (
        metrics["max_positive_contribution_share"] > thresholds["conditional_contribution_share_gt"]
        or metrics["severe_equity_ratio"] < thresholds["conditional_severe_equity_ratio_lt"]
        or bool(metrics["neighbor_sign_reversal"])
    )
    passes = (
        metrics["frozen_test_excess_return"] > thresholds["pass_frozen_test_excess_return_gt"]
        and metrics["stitched_oos_return"] > thresholds["pass_stitched_oos_return_gt"]
        and metrics["stitched_max_drawdown"] >= thresholds["pass_stitched_max_drawdown_ge"]
        and metrics["moderate_equity_ratio"] >= thresholds["pass_moderate_equity_ratio_ge"]
    )
    return "PASS" if passes and not conditional else "CONDITIONAL"
