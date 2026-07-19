"""Command line entry points for reproducible experiments."""

import argparse
from .experiment import ExperimentConfig, initialize_output


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m kelaode.experiment_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "grid-search", "walk-forward"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    config = ExperimentConfig.from_json(args.config)
    if config.strategy_class == "SITMomentumRotationStrategy":
        from .sit_validation import run_validation

        path = run_validation(args.config, args.command)
        print(f"{args.command}: {path}")
        return 0
    path = initialize_output(config)
    print(f"{args.command}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
