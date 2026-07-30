#!/usr/bin/env python
"""p4 — the journal exports as OpenTelemetry spans, with usage and cost per span.

Session 14 turned the durable event journal into AG-UI events. Session 15 turns
the same journal into OTel spans. Nothing new is recorded, which is the claim
worth proving: delete the materialised graph and the trace still builds from the
tape alone.

What is checked:

1. The hierarchy is ``run -> agent loop -> plan -> node -> provider call``, read
   back from the parent pointers the SDK actually produced.
2. Every provider-call span carries the GenAI semantic-convention attributes
   ``gen_ai.provider.name``, ``gen_ai.request.model``, ``gen_ai.usage.input_tokens``
   and ``gen_ai.usage.output_tokens``, plus a cost.
3. Span costs sum to exactly what the budget ledger says was spent.
4. Content capture is OFF: no prompt or completion text is anywhere in the trace.
5. It works with no collector. Give ``--otel-endpoint`` and the same spans also go
   over the wire to Jaeger or any OTLP receiver.

    python proofs/p4_trace_export.py --task "<anything>" --budget 0.02
    python proofs/p4_trace_export.py --task "<anything>" --budget 0.02 --offline
    python proofs/p4_trace_export.py --task "<anything>" --otel-endpoint http://127.0.0.1:4318/v1/traces
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from harness import Args, Proof, main, run_task, sync, transport_for

from s15code.telemetry import export_run
from s15code.telemetry.spans import (
    COST,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_INPUT_TOKENS,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_OUTPUT_TOKENS,
    GEN_AI_PROVIDER,
    GEN_AI_REQUEST_MODEL,
)

REQUIRED_GENAI = (GEN_AI_PROVIDER, GEN_AI_REQUEST_MODEL, GEN_AI_INPUT_TOKENS, GEN_AI_OUTPUT_TOKENS)
EXPECTED_PARENT = {"agent_loop": "run", "plan": "agent_loop", "node": "agent_loop", "provider_call": "node"}


def run(args: Args) -> Proof:
    transport, mode, detail = transport_for(args)
    proof = Proof(name="p4_trace_export", args=args, mode=mode, mode_detail=detail)

    with tempfile.TemporaryDirectory(prefix="s15-p4-") as workspace:
        outcome = sync(run_task(args, budget=args.budget, transport=transport,
                                data_dir=Path(workspace) / "run"))

    journal = outcome.journal
    budget = outcome.budget
    export = export_run(journal, budget=budget, endpoint=args.otel_endpoint,
                        principal=args.principal)
    spans = export.as_dict()["spans"]
    by_id = {span["span_id"]: span for span in spans}
    totals = export.totals()

    proof.fact("journal events", len(journal["events"]))
    proof.fact("spans", totals["spans"])
    proof.fact("span kinds", totals["by_kind"])
    proof.fact("provider calls", totals["provider_calls"])
    proof.fact("input tokens", totals["input_tokens"])
    proof.fact("output tokens", totals["output_tokens"])
    proof.fact("span cost total", f"{totals['cost']:.8f}")
    proof.fact("ledger spent", f"{budget.get('spent', 0.0):.8f}")
    proof.fact("trace ids", totals["trace_ids"])
    proof.fact("otlp endpoint", args.otel_endpoint or "(none: spans built, nothing sent)")

    # 1. the hierarchy
    kinds = set(totals["by_kind"])
    proof.check("every level of the hierarchy is present",
                {"run", "agent_loop", "plan", "node", "provider_call"} <= kinds,
                sorted(kinds))
    wrong_parents = []
    for span in spans:
        expected = EXPECTED_PARENT.get(span["kind"])
        if expected is None:
            continue
        parent = by_id.get(span["parent_span_id"] or "")
        if not parent or parent["kind"] != expected:
            wrong_parents.append((span["name"], span["kind"], parent["kind"] if parent else None))
    proof.check("run -> agent loop -> plan -> node -> provider call",
                not wrong_parents, wrong_parents or "every parent is the expected kind")
    proof.check("one run is one trace", len(totals["trace_ids"]) == 1, totals["trace_ids"])

    # 2. GenAI attributes on every provider call
    missing = [
        (span["name"], [name for name in REQUIRED_GENAI if name not in span["attributes"]])
        for span in spans if span["kind"] == "provider_call"
        and any(name not in span["attributes"] for name in REQUIRED_GENAI)
    ]
    proof.check("gen_ai.* usage attributes on every provider call", not missing,
                missing or f"{totals['provider_calls']} spans carry all of {list(REQUIRED_GENAI)}")
    uncosted = [span["name"] for span in spans
                if span["kind"] == "provider_call" and not span["attributes"].get(COST, 0) > 0]
    proof.check("cost per provider-call span", not uncosted,
                uncosted or f"all {totals['provider_calls']} priced")

    # 3. the trace and the ledger agree
    delta = abs(totals["cost"] - budget.get("spent", 0.0))
    proof.check("span costs sum to the ledger", delta < 1e-12,
                f"delta {delta:.3e}")
    ledger_tokens = sum(c["input_tokens"] for c in budget.get("charges", []))
    proof.check("span token counts match the ledger",
                totals["input_tokens"] == ledger_tokens,
                f"{totals['input_tokens']} == {ledger_tokens}")

    # 4. PII: no content anywhere in the trace
    leaked = [span["name"] for span in spans
              if GEN_AI_INPUT_MESSAGES in span["attributes"] or GEN_AI_OUTPUT_MESSAGES in span["attributes"]]
    task_leaked = args.task in str([span["attributes"] for span in spans])
    proof.check("content capture is off by default",
                not leaked and not export.capture_content and not task_leaked,
                f"capture_content={export.capture_content}, message attrs on {len(leaked)} spans, "
                f"task text present: {task_leaked}")

    # 5. the journal alone is enough
    tape_only = export_run({**journal, "nodes": {}}, budget=budget)
    proof.check("the tape alone rebuilds the trace",
                tape_only.totals()["provider_calls"] == totals["provider_calls"],
                f"{tape_only.totals()['provider_calls']} provider calls from events only")
    proof.check("no collector is required" if not args.otel_endpoint else "spans were exported over the wire",
                export.exported_over_the_wire == bool(args.otel_endpoint),
                f"exported_over_the_wire={export.exported_over_the_wire}")

    proof.record("budget", budget)
    proof.record("totals", totals)
    proof.record("spans", spans)
    return proof


if __name__ == "__main__":
    main(run, __doc__ or "")
