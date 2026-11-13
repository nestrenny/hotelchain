"""Pipeline orchestrator (stands in for Data Factory / Fabric pipeline orchestration).

    python -m hip.pipelines.run all        # generate -> bronze -> silver -> gold -> quality -> ai
    python -m hip.pipelines.run bronze     # a single stage (also: generate, silver, gold, quality, ai)
"""
from __future__ import annotations

import sys
import time
import warnings

warnings.filterwarnings("ignore")

STAGES = ["generate", "bronze", "silver", "gold", "quality", "ai"]


def main(argv: list[str]) -> None:
    want = argv[1:] or ["all"]
    stages = STAGES if "all" in want else [s for s in STAGES if s in want]
    t0 = time.time()
    if "generate" in stages:
        from hip.synth import generate
        generate.main()
        # Regenerating rewrites source history, so incremental watermarks no longer apply: start from an empty lake.
        from hip.settings import LAKE
        for f in (LAKE, LAKE.with_suffix(".duckdb.wal")):
            f.unlink(missing_ok=True)
    from hip import lake
    con = lake.connect()
    run_id = lake.new_run_id()
    if "bronze" in stages:
        from hip.pipelines import bronze
        bronze.run(con, run_id)
    if "silver" in stages:
        from hip.pipelines import silver
        silver.run(con, run_id)
    if "gold" in stages:
        from hip.pipelines import gold
        gold.run(con, run_id)
    if "quality" in stages:
        from hip.pipelines import quality
        quality.run(con, run_id)
    if "ai" in stages:
        from hip.ai import run as ai_run
        ai_run.run(con, run_id)
        from hip.pipelines import gold
        gold.build_catalog(con, run_id)
    con.close()
    print(f"Run {run_id} finished in {time.time() - t0:.0f}s: {', '.join(stages)}")


if __name__ == "__main__":
    main(sys.argv)
