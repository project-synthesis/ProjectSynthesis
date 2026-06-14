"""One-shot capture of pre-refactor resolve_strategy_intelligence formatted outputs
across the 10 parity scenarios. Run this against /tmp/strategy_intelligence_main.py
(pre-refactor source from main) to produce the EXPECTED_OUTPUTS constants for
backend/tests/test_preview_enrichment.py.

Usage (from backend/ with venv active):
  python /tmp/capture_parity.py > /tmp/parity_capture.txt
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

# Load pre-refactor module under a synthetic name (so it doesn't collide with the
# installed app.services.strategy_intelligence).
PRE_REFACTOR_PATH = Path("/tmp/strategy_intelligence_main.py")

BACKEND_DIR = Path("/home/drei/my_project/builder/claude-quickstarts/autonomous-coding/generations/PromptForge_v2/backend")
sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

# We want to import the pre-refactor module, but it imports from app.* — those
# resolve correctly because we cd'd into backend/.
spec = importlib.util.spec_from_file_location(
    "_pre_refactor_strategy_intelligence", str(PRE_REFACTOR_PATH),
)
assert spec is not None and spec.loader is not None
pre_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre_mod)
resolve_pre = pre_mod.resolve_strategy_intelligence

# Same scenarios as the test.
SCENARIOS: list[tuple[str, str]] = [
    ("coding", "backend"),
    ("coding", "frontend"),
    ("writing", "general"),
    ("analysis", "data"),
    ("creative", "general"),
    ("data", "database"),
    ("system", "devops"),
    ("general", "general"),
    ("coding", "security"),
    # 10th scenario is duplicate task_type/domain with different seed data,
    # handled inline as a separate db session in the real test. We capture
    # it under a distinct key.
]


async def main() -> None:
    # Use the test's in-memory engine setup to mirror the test environment.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Optimization, StrategyAffinity

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    out: dict[str, tuple[str | None, bool]] = {}

    async with Session() as db:
        # Seed exactly the data the test seeds, scenario-by-scenario, then capture.
        scenario_data = [
            ("coding", "backend",
             [("meta-prompting", 8.0, 3)], [("bad-a", 1, 10)]),
            ("coding", "frontend", [], []),
            ("writing", "general",
             [("few-shot", 7.5, 3)], [("bad-b", 0, 8)]),
            ("analysis", "data",
             [("chain-of-thought", 8.2, 3)], []),
            ("creative", "general", [], [("bad-c", 1, 10)]),
            ("data", "database",
             [("structured-output", 7.8, 4)], []),
            ("system", "devops",
             [("role-playing", 6.5, 3)], []),
            ("general", "general", [], []),
            ("coding", "security",
             [("alpha", 9.0, 3), ("beta", 8.5, 3), ("gamma", 7.8, 3)], []),
            ("writing", "general",
             [("few-shot", 4.0, 4)], [("bad-d", 0, 6)]),
        ]
        from datetime import UTC, datetime
        for idx, (tt, dom, opts, affs) in enumerate(scenario_data):
            for strategy, score, n in opts:
                for _ in range(n):
                    db.add(Optimization(
                        raw_prompt=f"test prompt for {strategy}",
                        optimized_prompt="optimized",
                        overall_score=score,
                        strategy_used=strategy,
                        task_type=tt, domain=dom,
                        status="completed",
                    ))
            for strategy, up, down in affs:
                total = up + down
                db.add(StrategyAffinity(
                    task_type=tt,
                    strategy=strategy,
                    thumbs_up=up,
                    thumbs_down=down,
                    approval_rate=up / total if total > 0 else 0.0,
                ))
            await db.commit()
            text, fb = await resolve_pre(db, tt, dom)
            key = f"{tt}|{dom}|{idx}"
            out[key] = (text, fb)

    print(json.dumps(out, indent=2, ensure_ascii=False))


asyncio.run(main())
