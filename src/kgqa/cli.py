from __future__ import annotations

import argparse
import json

from kgqa.config import get_settings
from kgqa.service import KGQAService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="kg-qa-poc command line")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser("ask", help="Ask one question")
    ask_parser.add_argument("question", help="Natural language question")

    subparsers.add_parser("seed-load", help="Load seed data into Neo4j")
    subparsers.add_parser("eval-run", help="Run local evaluation and generate HTML report")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()

    if args.command == "seed-load":
        KGQAService(settings).load_seed_data()
        print("Seed data loaded.")
        return

    if args.command == "eval-run":
        from eval.run_eval import run_evaluation

        report = run_evaluation()
        print(f"Evaluation report generated at {report}")
        return

    response = KGQAService(settings).process_question(args.question)
    print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
