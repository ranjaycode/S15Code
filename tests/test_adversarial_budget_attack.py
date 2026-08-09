"""Adversarial budget attack test suite for Session 15 Part 3.

Drives runaway graph execution loops, unaffordable tier requests, and ladder cascade
exhaustion under controlled ceilings to prove budget controls hold and refusals
are recorded in OpenTelemetry span telemetry.
"""

import pytest
from s15code.economics import (
    EconomicsConfig,
    RunBudget,
    BudgetPolicy,
    BudgetRefused,
)
from s15code.telemetry import export_run


def test_adversarial_runaway_loop_refused_and_bounded():
    """Attack 1: An infinite graph loop attempting unlimited nodes stops at ceiling."""
    config = EconomicsConfig.load()
    budget = RunBudget(
        total=0.002,
        principal="test/attacker",
    )
    policy = BudgetPolicy(config.ladder, config.pricing, config.thresholds)

    admitted = 0
    refusals = 0

    for i in range(50):
        decision = policy.decide(
            node_id=f"node_{i}",
            requested_tier="standard",
            budget=budget,
        )
        if decision.action in ("proceed", "downgrade", "branch"):
            tier = decision.tier or config.ladder.tier("economy")
            projected = policy.project(tier)
            if budget.can_admit(f"node_{i}", projected):
                admitted += 1
                budget.charge(
                    node_id=f"node_{i}",
                    role=f"node_{i}",
                    tier=tier.name,
                    requested_tier="standard",
                    model=tier.model,
                    provider=tier.request.get("provider"),
                    input_tokens=150,
                    output_tokens=300,
                    pricing=config.pricing,
                )
            else:
                refusals += 1
                budget.record_refusal(f"node_{i}", f"budget cannot admit call at tier {tier.name}")
        else:
            refusals += 1
            budget.record_refusal(f"node_{i}", decision.reason or "budget_refusal")

    # Invariants
    assert budget.spent <= 0.002, f"Ledger overspent: {budget.spent} > 0.002"
    assert admitted < 50, f"Runaway loop was not throttled (admitted {admitted} nodes)"
    assert refusals > 0, "No refusal was recorded during runaway loop attack"
    assert len(budget.refusals) == refusals


def test_adversarial_unaffordable_tier_request_refused():
    """Attack 2: Requesting tier 'frontier' with a tiny budget ($0.0001) triggers refusal."""
    config = EconomicsConfig.load()
    budget = RunBudget(
        total=0.0001,
        principal="test/attacker",
    )
    policy = BudgetPolicy(config.ladder, config.pricing, config.thresholds)

    decision = policy.decide(
        node_id="node_expensive",
        requested_tier="frontier",
        budget=budget,
    )

    assert decision.action == "refuse", f"Policy did not refuse: {decision.action}"
    assert decision.reason is not None, "Refusal reason is missing"


def test_adversarial_refusal_visible_in_opentelemetry_spans():
    """Attack 3: Verify budget refusals export as explicit failure spans in OpenTelemetry."""
    journal_dict = {
        "run_id": "run-adv-01",
        "finished": True,
        "nodes": {
            "node_adv": {
                "id": "node_adv",
                "skill": "adversarial_node",
                "state": "failed",
                "metadata": {"tier": "frontier"},
                "input": {},
                "result": {"error": "BudgetRefused: budget 0.0001 < projected call 0.032"},
            }
        },
        "edges": (),
        "events": [
            {"sequence": 1, "kind": "run_started", "node_id": None, "payload": {}},
            {
                "sequence": 2,
                "kind": "task_failed",
                "node_id": "node_adv",
                "payload": {
                    "error_type": "BudgetRefused",
                    "error_message": "budget 0.00010000 < projected call 0.03200000 at tier frontier",
                    "budget_decisions": [
                        {
                            "action": "refuse",
                            "tier": "frontier",
                            "requested_tier": "frontier",
                            "reason": "budget 0.00010000 < projected call 0.03200000",
                        }
                    ],
                },
            },
        ],
    }

    exported = export_run(journal_dict, endpoint=None, capture_content=False)
    spans = exported.spans
    assert len(spans) > 0, "No spans generated"
    refusal_spans = [s for s in spans if "refuse" in str(s).lower() or "failed" in str(s).lower() or not s.status.is_ok]
    assert len(refusal_spans) > 0, "Budget refusal missing from exported OpenTelemetry span tree"
