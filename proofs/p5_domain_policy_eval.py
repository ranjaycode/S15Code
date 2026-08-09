#!/usr/bin/env python
"""p5 — Domain policy evaluation for Session 15 Part 2.

Evaluates the custom 15-task cloud security operations workload (cloud_security_ops.jsonl)
across Always-Frontier, Cheap-with-Retries, and Budget-Cascade strategies. Writes the
comprehensive evaluation findings to proofs/out/p5_report.json.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parents[1]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from proofs.p1_cost_per_task import main as run_p1_eval


def main() -> None:
    parser = argparse.ArgumentParser(description="Session 15 Part 2 Domain Policy Evaluation")
    parser.add_argument("--tasks", type=str, default=str(root_dir / "proofs" / "tasks" / "cloud_security_ops.jsonl"))
    parser.add_argument("--offline", action="store_true", help="Run in deterministic offline mode")
    parser.add_argument("--principal", type=str, default="proofs/s15/p5")
    parser.add_argument("--base-url", type=str, default=None)
    parser.add_argument("--otel-endpoint", type=str, default=None)
    parser.add_argument("--config-dir", type=str, default=None)

    args = parser.parse_args()

    sys_argv = [
        "p1_cost_per_task.py",
        "--tasks", args.tasks,
        "--principal", args.principal,
    ]
    if args.offline:
        sys_argv.append("--offline")
    if args.base_url:
        sys_argv.extend(["--base-url", args.base_url])
    if args.otel_endpoint:
        sys_argv.extend(["--otel-endpoint", args.otel_endpoint])
    if args.config_dir:
        sys_argv.extend(["--config-dir", args.config_dir])

    old_argv = sys.argv
    sys.argv = sys_argv
    try:
        run_p1_eval()
    finally:
        sys.argv = old_argv

    # Copy output to p5_report.json if p1_report.json exists
    p1_out = root_dir / "proofs" / "out" / "p1_report.json"
    p5_out = root_dir / "proofs" / "out" / "p5_report.json"
    if p1_out.exists():
        p5_out.write_text(p1_out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nWrote domain policy evaluation findings to {p5_out}")


if __name__ == "__main__":
    main()
