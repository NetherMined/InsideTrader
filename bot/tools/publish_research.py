"""Load test results from bot/results/*.json into the research_results table for the Test Results page.

Run: python -m bot.tools.publish_research [--dir bot/results]

Each file is one result set: {"key", "title", "description", "phase", "updated_at", "rows": [...]}.
Existing keys are replaced, so re-running after editing a file updates the page.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from bot.db.connection import async_session, engine
from bot.db.models import Base, ResearchResult

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "results"
REQUIRED = ("key", "title", "rows")


def read_results(directory: Path) -> list[dict]:
    """Parse and validate every result file in `directory`, sorted by file name."""
    docs = []
    for path in sorted(directory.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if k not in doc]
        if missing:
            raise ValueError(f"{path.name}: missing {', '.join(missing)}")
        if not isinstance(doc["rows"], list):
            raise ValueError(f"{path.name}: rows must be a list")
        if doc["key"] != path.stem:
            raise ValueError(f"{path.name}: key '{doc['key']}' must match the file name")
        docs.append({k: doc.get(k) for k in ("key", "title", "description", "phase", "updated_at", "rows")})
    return docs


async def publish(docs: list[dict]) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[ResearchResult.__table__]))
    async with async_session() as session:
        for doc in docs:
            stmt = insert(ResearchResult).values(**doc)
            await session.execute(stmt.on_conflict_do_update(
                index_elements=[ResearchResult.key],
                set_={k: stmt.excluded[k] for k in ("title", "description", "phase", "updated_at", "rows")}))
        await session.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    args = ap.parse_args()
    docs = read_results(Path(args.dir))
    asyncio.run(publish(docs))
    for d in docs:
        print(f"  {d['key']}: {len(d['rows'])} rows")
    print(f"Published {len(docs)} result sets")


if __name__ == "__main__":
    main()
