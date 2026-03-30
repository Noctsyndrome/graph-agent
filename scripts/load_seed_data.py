from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from kgqa.config import get_settings
from kgqa.query import Neo4jExecutor


def main() -> None:
    settings = get_settings()
    seed_file = ROOT / "data" / "seed_data.cypher"
    if not seed_file.exists():
        raise FileNotFoundError("seed_data.cypher 不存在，请先运行 scripts/generate_seed_data.py")

    executor = Neo4jExecutor(settings)
    executor.load_seed_data(seed_file.read_text(encoding="utf-8"))
    print("Seed data loaded successfully.")


if __name__ == "__main__":
    main()
