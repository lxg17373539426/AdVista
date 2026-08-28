from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1] / "outputs" / "benchmarks" / "mac3108"
BASELINE_SECONDS = 6468.47


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def projected_full_seconds(summary: dict[str, Any]) -> float | None:
    count = int(summary.get("completed_items", 0))
    wall = float(summary.get("wall_seconds", 0))
    return wall * 3108 / count if count and wall else None


def main() -> None:
    rows = []
    for path in sorted(ROOT.glob("*/summary.json")):
        summary = read_json(path)
        projected = projected_full_seconds(summary)
        rows.append(
            {
                "experiment": summary.get("experiment", path.parent.name),
                "items": summary.get("completed_items", 0),
                "wall_seconds": summary.get("wall_seconds", 0),
                "items_per_second": summary.get("items_per_second", 0),
                "projected_full_seconds": projected,
                "speedup_vs_historical": BASELINE_SECONDS / projected if projected else None,
                "format_pass_rate": summary.get("format_pass_rate", 0),
                "official_token_f1_mean": summary.get("official_token_f1_mean"),
                "official_point_count_match_rate": summary.get("official_point_count_match_rate"),
                "failed_items": summary.get("failed_items", 0),
                "repair_request_count": summary.get("repair_request_count", 0),
                "configuration": summary.get("configuration", {}),
            }
        )
    output = ROOT / "optimization_report.json"
    output.write_text(json.dumps({"historical_baseline_seconds": BASELINE_SECONDS, "experiments": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "experiments": len(rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
