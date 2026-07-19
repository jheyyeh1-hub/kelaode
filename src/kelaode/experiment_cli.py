"""Command line entry points for reproducible experiments."""

import argparse
from .experiment import ExperimentConfig
from .runner import run_experiment


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m kelaode.experiment_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = ExperimentConfig.from_json(args.config)
    path = run_experiment(config)
    print(f"{args.command}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
