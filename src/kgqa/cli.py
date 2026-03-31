from __future__ import annotations

import argparse

from kgqa.config import get_settings
from kgqa.query import load_seed_data
from kgqa.scenario import build_scenario_settings, get_scenario_definition


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="kg-qa-poc command line")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed-load", help="Load seed data into Neo4j")
    seed_parser.add_argument("--scenario", default=None, help="Scenario id to load, e.g. hvac or elevator")
    eval_parser = subparsers.add_parser("eval-run", help="Run local evaluation and generate HTML report")
    eval_parser.add_argument("--scenario", default=None, help="Scenario id to evaluate, e.g. hvac or elevator")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()

    if args.command == "seed-load":
        scenario = get_scenario_definition(args.scenario)
        load_seed_data(build_scenario_settings(settings, scenario))
        print("Seed data loaded.")
        return

    if args.command == "eval-run":
        from eval.run_eval import run_evaluation

        report = run_evaluation(scenario_id=args.scenario)
        print(f"Evaluation report generated at {report}")
        return
