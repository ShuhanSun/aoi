"""Summarize externally graded ITBench AOI task records."""

import argparse
import json
from pathlib import Path


def summarize(records: list[dict]) -> dict:
    verified = [r for r in records if isinstance(r.get("success"), bool)]
    completed = [r for r in verified if r["success"]]
    def average(values):
        return round(sum(values) / len(values), 4) if values else None
    return {
        "benchmark": "ITBench", "domain": "SRE",
        "total_attempts": len(records), "verified_attempts": len(verified),
        "unverified_attempts": len(records) - len(verified),
        "verified_successes": len(completed),
        "success_rate": len(completed) / len(verified) if verified else None,
        "mean_duration_seconds": average([r["duration_seconds"] for r in verified]),
        "mean_tool_calls": average([len(r.get("tool_calls", [])) for r in verified]),
        "mean_cost_usd": average([
            r["cost_usd"] for r in verified if isinstance(r.get("cost_usd"), (int, float))
        ]),
        "cost_coverage": sum(isinstance(r.get("cost_usd"), (int, float)) for r in verified),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize verified ITBench task results")
    parser.add_argument("record_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    paths = sorted(args.record_dir.glob("sre-*.json"))
    records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    if any(record.get("benchmark") != "ITBench" for record in records):
        raise ValueError("Record directory contains a non-ITBench task record")
    summary = summarize(records)
    payload = json.dumps(summary, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
