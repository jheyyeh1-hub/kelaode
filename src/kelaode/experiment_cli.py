"""Command line entry points for reproducible experiments."""

import argparse
import sys
from .experiment import ExperimentConfig
from .runner import run_experiment
from .selection_runner import run_fixed_selection, run_walk_forward


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m kelaode.experiment_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "grid-search", "walk-forward"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        config = ExperimentConfig.from_json(args.config)
        expected = {"run": "run", "grid-search": "fixed_selection", "walk-forward": "walk_forward"}[args.command]
        if config.experiment_mode != expected:
            detail = (f"{args.command} is unavailable for schema 2.0 with this configuration; "
                      f"it requires experiment_mode={expected}")
            raise ValueError(detail)
        path = {"run": run_experiment, "grid-search": run_fixed_selection,
                "walk-forward": run_walk_forward}[args.command](config)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"configuration/data/artifact failure: {exc}", file=sys.stderr)
        return 2
    print(f"{args.command}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
