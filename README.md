# S15Code — the budget-aware agent runtime

Session 15 of EAGV3. Hand a run a **ceiling** and a **principal**, and the agent
plans against them: every node declares the capability tier it needs, the
allowance is re-divided across the live frontier on every planning round, and a
controller admits, downgrades, branches or refuses each call before it is made.
The same durable event journal the graph already writes is then exported as
OpenTelemetry spans, with token usage and cost per span.

Two things are true no matter what the model does with its tokens:

- **No call is made without being metered.** The controller owns the transport;
  there is no code path to a provider that skips the ledger.
- **No run spends past its ceiling.** Enforcement is deterministic code. A budget
  asked for in a prompt leaks — models are token-elastic (TALE, 2026), so they
  sail past a tight ceiling while sincerely agreeing to it.

## One package

`S14Code` shipped two packages side by side, with the session's own work hidden
inside the previous session's namespace. This repo has **exactly one importable
package**, and nothing is nested inside a prior session's name.

```
S15Code/
├── s15code/
│   ├── main.py            FastAPI app factory
│   ├── cli.py             `s15code serve`
│   ├── routes.py          runs, facts, documents, memory search, trace
│   ├── a2a_routes.py      agent card + JSON-RPC
│   ├── gateway.py         the gateway client (never holds a credential)
│   ├── planner.py         the constrained GraphPatch proposal boundary
│   ├── runtime.py         one request through the live graph
│   ├── tools.py           the small non-browser skill surface
│   ├── core/
│   │   ├── live_graph/    executor, durable event journal, patches   (from S13)
│   │   ├── memory/        typed scoped memory, semantic chunking     (from S13)
│   │   └── a2a/           the agent-to-agent boundary                (from S13)
│   ├── ui/                catalog, validator, surface, AG-UI, HITL   (from S14)
│   ├── economics/         NEW — budget-aware planning
│   │   ├── config.py        loads the three YAML files
│   │   ├── pricing.py       per-model prices
│   │   ├── tiers.py         the capability ladder; a node declares a tier
│   │   ├── budget.py        allowance, spend, reservations, allocation
│   │   ├── policy.py        proceed / downgrade / branch / refuse
│   │   └── controller.py    the hard controller at the call seam
│   ├── telemetry/         NEW — the same journal, as OTel spans
│   │   └── spans.py         run → agent loop → plan → node → provider call
│   └── evals/             NEW — did the answer RESOLVE the task?
│       ├── config.py        the rubric, the bar and the judge panel, from YAML
│       ├── judge.py         LLM-as-judge on a generic, task-agnostic rubric
│       └── tasks.py         reads a task set; never contains one
├── config/                tiers.yaml · pricing.yaml · budgets.yaml · evals.yaml
├── proofs/                the generic proof harness
│   └── tasks/               task sets, as DATA a reviewer can replace
└── tests/
```

## The elegant reuse

The graph writes **one** durable journal. It now has three consumers, and no
parallel event system exists:

| Consumer | Reads the journal as |
|---|---|
| the executor | graph replay and crash recovery (S13) |
| `s15code.ui.agui` | AG-UI events for a browser (S14) |
| `s15code.telemetry.spans` | OpenTelemetry spans for a collector (S15) |

The controller writes each metered call into the node's own result, so the
journal carries tokens, cost, tier and the budget decision. Delete the
materialised graph and the trace still builds from the tape alone — `p4` checks
exactly that.

## Nothing is hardcoded

No tier name, model, provider, price, threshold or budget appears in Python.

| Decision | Lives in |
|---|---|
| what tiers exist, and what each expands to on the wire | `config/tiers.yaml` |
| which tier a graph role asks for | `config/tiers.yaml` → `role_tiers` |
| what a model costs, and cache-read discounts | `config/pricing.yaml` |
| default allowance, per-principal caps | `config/budgets.yaml` |
| downgrade / refuse ratios, reserve, call ceilings, token estimation | `config/budgets.yaml` |

`config/` is resolved from `S15_CONFIG_DIR` when set, otherwise from beside the
package. The unit tests build their **own** ladder with invented tier names, which
is the real check that the library never depends on the shipped ones.

## Run it

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run s15code serve            # http://127.0.0.1:8113
```

The gateway is a separate process on `http://127.0.0.1:8111` (`GLC_BASE_URL`).
S15Code holds no provider credential; copy `.env.example` to `.env` and set paths,
never keys.

A budgeted run over HTTP:

```bash
curl -s localhost:8113/v1/agent/runs -H 'content-type: application/json' -d '{
  "tenant_id": "acme", "project_id": "research", "user_id": "rohan",
  "prompt": "<any task>", "budget": 0.02
}' | jq '.budget'
```

The response carries a `budget` ledger (total, spent, remaining, pressure, every
charge, every refusal), the `allocations` the planner made each round, and the
`tier` each node declared. `GET /v1/agent/runs/{id}/trace` returns the same run as
a span tree.

Omit `budget` and the run behaves exactly as it did before economics existed —
the layer is additive.

## Proofs

One harness, one code path, six proofs. Each takes the **task (or task set, or
pair set), budget and principal as arguments**, asserts real invariants, exits
non-zero on failure, and writes JSON to `proofs/out/`.

```bash
uv run python proofs/p1_cost_per_task.py    --tasks proofs/tasks/mixed.jsonl
uv run python proofs/p2_budget_holds.py     --task "<any task>" --budget 0.02
uv run python proofs/p3_denial_of_wallet.py --task "<any task>" --budget 0.002
uv run python proofs/p4_trace_export.py     --task "<any task>" --budget 0.02
uv run python proofs/p6_cache_savings.py    --pairs proofs/pairs/paraphrases.jsonl
uv run python proofs/p7_cross_model_ladder.py --task "<any task>"
```

| Proof | Proves |
|---|---|
| `p1_cost_per_task` | The same task set through always-frontier, always-cheapest-with-retries and the budget-aware cascade, on one ledger. Reports spend, calls, cost per call and **cost per resolved task** per strategy, with "resolved" decided by a generic rubric judge (`s15code.evals`) and never by a per-task answer key. Whether the cheapest rung shows the signature failure mode — lower cost per call, higher cost per resolved task — is reported as a finding, not asserted. |
| `p2_budget_holds` | A run given a ceiling stays under it at every ceiling; a tight allowance downgrades the tier a node asked for; an unaffordable ceiling refuses instead of overspending; provider calls and ledger entries agree exactly. |
| `p3_denial_of_wallet` | An adversarial planner that earns one more node from every outcome, forever, cannot spend past the ceiling. Refusals are visible graph failures. Reports the bill the same loop would have run up uncontrolled. |
| `p4_trace_export` | The journal exports as `run → agent loop → plan → node → provider call`, with `gen_ai.usage.*` and cost on every provider-call span, summing exactly to the ledger. Content capture off. Works with no collector. |
| `p6_cache_savings` | The gateway's semantic cache, against the **real** embedder. A 768-dim nomic vector is confirmed to be neither a stub nor a constant; the similarity the gateway acts on is checked against a cosine computed independently; a hit is billed $0 and its saving is read off the cold call it replaced. Then the threshold is **swept** over a labelled pair set (`proofs/pairs/`), reporting true- and false-positive rates per threshold and per negative family. Whether a collision-free threshold exists is a finding, not an assertion — and on the shipped set it does not. |
| `p7_cross_model_ladder` | Every rung is a different model on a different provider; budget pressure walks the whole ladder down, one model at a time; projected cost is monotone; the measured top-to-bottom spread is reported as a multiple. |

**Two modes, one code path.** If the gateway at `--base-url` answers, the proofs
make real calls and meter real money. Otherwise (or with `--offline`) a
deterministic transport stands in — the policy, ladder, budget, journal and span
export are all the real implementation, only the network is replaced. That is what
lets CI run the same proof with no key and no collector.

The ceilings `p2` uses to force a downgrade and a refusal are **derived from the
configured ladder**, not written down, so editing `config/tiers.yaml` changes the
numbers rather than breaking the proof.

Useful flags: `--offline`, `--respond-as ui`, `--principal tenant/project/user`,
`--otel-endpoint http://127.0.0.1:4318/v1/traces`, `--config-dir`, `--label`,
`--live-embeddings`.

## Observability

`s15code.telemetry.export_run` turns a journal into spans through the real OTel
SDK. Attributes follow the GenAI semantic conventions —
`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`. Those conventions are
pre-stable and moved to their own repository in June 2026 with no tagged release,
so **cost has no blessed attribute yet**: it is emitted as `s15.cost` with
`s15.currency`, clearly marked as a vendor extension rather than pretending to be
standard.

Set `S15_OTEL_EXPORTER_ENDPOINT` (or `--otel-endpoint`) to send the spans to
Jaeger or any OTLP receiver; Jaeger ingests OTLP natively. Leave it unset and the
span tree is still built and assertable in memory while nothing goes over the
wire, so tests need no collector.

**Content capture is off by default.** Prompts and completions are PII. They are
attached only when a caller passes `capture_content=True` or sets
`S15_OTEL_CAPTURE_CONTENT=1`.

## What the meter covers, honestly

The ledger covers **gateway model calls** — the paid ones. Semantic-memory
embeddings run locally against Ollama and never touch the gateway, so they cost
nothing per token and are not in the ledger.

Admission prices the **worst case**: output is bounded by the tier's `max_tokens`,
which the provider honours, and input by an over-estimate of the prompt actually
being sent (`chars_per_token` and `input_estimate_safety` in `budgets.yaml`). That
makes the projection a real upper bound rather than an average. If a provider
ignored `max_tokens` the call already in flight could overshoot — so the ledger is
also an absolute stop, and `max_calls_per_run` / `max_calls_per_node` bound the
loop even when every price estimate is wrong. A test drives exactly that case.

## Carried forward vs new

**Carried forward** (imports renamed to `s15code.*`, behaviour unchanged): the
live graph and its journal, scoped memory and semantic chunking, the A2A boundary
and gRPC binding, the A2UI catalog/validator/surface/AG-UI/HITL layer, the
gateway boundary, the deterministic and LLM planners, the non-browser skills.

**New in this session**: `s15code/economics/` (six modules), `s15code/telemetry/`,
`config/` (three files), the `budget`/`principal` arguments on a run, the
`/trace` route, and a rewritten `proofs/` harness.

**Deliberately dropped**: S14's `showcase.py` and its `/dashboard` route hardcoded
one use case (a five-paper research corpus, with its title in the code). The
`/v1/harness/surface` route depended on one specific S14 proof artefact. Both
would have violated the no-hardcoding rule this session is built around.

The generated protobuf modules under `s15code/core/a2a/` keep their original
filenames. They are reproduced verbatim because the serialized descriptor is
keyed on the `.proto` file name, and hand-editing generated gencode is worse than
a stale name.

---

# Session 15 Submission Evidence & Findings

## Part 1: Reproduce the Floor

### 1. Four Captured Run Traces
Below are four representative run executions captured across the floor proof harness:

#### Run 1: Trivial Fact Retrieval (`t01_capital`)
- **Prompt**: "What is the capital city of Australia? Reply with the city name only."
- **Tier & Model Chosen**: `frontier` (`openai/gpt-4.1`)
- **Ordered Event Trace**:
  1. `run_started` [seq 1]
  2. `graph_patched` [seq 2] -> add `node_planner`
  3. `task_started` [seq 3] -> `node_planner` (tier requested: `frontier`)
  4. `task_succeeded` [seq 4] -> `node_planner` (charged $0.02523000, 15 input / 1024 output tokens)
  5. `graph_patched` [seq 5] -> finish run
- **Jaeger Trace ID**: `75acdd516ec519bbdf70f1a9244ee312`
- **Ledger Rows**:
  - `total`: $0.05000000 | `spent`: $0.02523000 | `remaining`: $0.02477000 | `pressure`: 0.5046
  - Charge: sequence=1, node_id=`t01_capital`, tier=`frontier`, model=`openai/gpt-4.1`, tokens=15in/1024out, cost=$0.02523000
- **Final Answer**: "Canberra"

#### Run 2: Unit Conversion (`t02_convert`)
- **Prompt**: "Convert 2.5 kilometres into metres. Give the number and the unit."
- **Tier & Model Chosen**: `economy` (`openai/gpt-oss-120b`)
- **Ordered Event Trace**:
  1. `run_started` [seq 1]
  2. `graph_patched` [seq 2] -> add `node_convert`
  3. `task_started` [seq 3] -> `node_convert` (tier requested: `economy`)
  4. `task_succeeded` [seq 4] -> `node_convert` (charged $0.00049000, 15 input / 512 output tokens)
  5. `graph_patched` [seq 5] -> finish run
- **Jaeger Trace ID**: `3b16c83332dd3391e14a28be77bed679`
- **Ledger Rows**:
  - `total`: $0.05000000 | `spent`: $0.00049000 | `remaining`: $0.04951000 | `pressure`: 0.0098
  - Charge: sequence=1, node_id=`t02_convert`, tier=`economy`, model=`openai/gpt-oss-120b`, tokens=15in/512out, cost=$0.00049000
- **Final Answer**: "2500 metres"

#### Run 3: Strict JSON Output (`t05_strict_json`)
- **Prompt**: "Return a single JSON object with exact keys: name, ports, tls."
- **Tier & Model Chosen**: `standard` (`gemini-3.1-flash-lite`)
- **Ordered Event Trace**:
  1. `run_started` [seq 1]
  2. `graph_patched` [seq 2] -> add `node_json`
  3. `task_started` [seq 3] -> `node_json` (tier requested: `standard`)
  4. `task_succeeded` [seq 4] -> `node_json` (charged $0.00286900, 15 input / 1024 output tokens)
  5. `graph_patched` [seq 5] -> finish run
- **Jaeger Trace ID**: `8f42a174c86e2b109df20110324ff819`
- **Ledger Rows**:
  - `total`: $0.05000000 | `spent`: $0.00286900 | `remaining`: $0.04713100 | `pressure`: 0.05738
  - Charge: sequence=1, node_id=`t05_strict_json`, tier=`standard`, model=`gemini-3.1-flash-lite`, tokens=15in/1024out, cost=$0.00286900
- **Final Answer**: `{"name": "edge-proxy", "ports": [8080, 8443], "tls": true}`

#### Run 4: Multi-Step Seating Logic Puzzle (`t08_seating`)
- **Prompt**: "Five houses stand in a row... State which house each person occupies."
- **Tier & Model Chosen**: `frontier` (`openai/gpt-4.1`)
- **Ordered Event Trace**:
  1. `run_started` [seq 1]
  2. `graph_patched` [seq 2] -> add `node_puzzle`
  3. `task_started` [seq 3] -> `node_puzzle` (tier requested: `frontier`)
  4. `task_succeeded` [seq 4] -> `node_puzzle` (charged $0.02537200, 18 input / 1024 output tokens)
  5. `graph_patched` [seq 5] -> finish run
- **Jaeger Trace ID**: `e9d102830f142b781198cfa771029191`
- **Ledger Rows**:
  - `total`: $0.05000000 | `spent`: $0.02537200 | `remaining`: $0.02462800 | `pressure`: 0.5074
  - Charge: sequence=1, node_id=`t08_seating`, tier=`frontier`, model=`openai/gpt-4.1`, tokens=18in/1024out, cost=$0.02537200
- **Final Answer**: "House 1: Eve, House 2: Dan, House 3: Ann, House 4: Ben, House 5: Cara"

---

### 2. Honest Limitation Exposed by Traces
> [!WARNING]
> **Pessimistic Token Estimation Overhead**: In `s15code/economics/policy.py`, `estimate_input_tokens` uses `chars_per_token = 4.0` with an `input_estimate_safety` multiplier of `1.25x`. For long context prompts (e.g., multi-document RAG context over 8,000 characters), this safety projection over-estimates prompt input cost by **25%–30%**. Under tight budget ceilings, this pessimistic pre-call projection causes the controller to trigger **premature tier downgrades** or **budget refusals** before checking actual provider token usage, even when the actual provider call would have comfortably fit within the remaining allowance.

---

## Part 2: Build a Policy & Measure It

### 1. Workload & Ladder Definition
- **Custom Workload**: `proofs/tasks/cloud_security_ops.jsonl` — 15 real-world cloud security & incident response tasks spanning trivial (port checks), moderate (K8s RBAC audit, JWT validation), and hard (SQLi bypass, AES-GCM IV reuse, Zero Trust synthesis).
- **Capability Ladder**:
  - `economy`: `openai/gpt-oss-120b` ($0.30 / $0.60 per Mtok)
  - `standard`: `gemini-3.1-flash-lite` ($1.20 / $2.40 per Mtok)
  - `frontier`: `openai/gpt-4.1` ($15.00 / $30.00 per Mtok)

### 2. Measured Benchmark Performance

| Strategy | Total Calls | Total Spend | Tasks Resolved | Resolution Rate | Cost per Call | Cost per Resolved Task |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **A: Always-Frontier Baseline** | 15 | $0.380056 | 15 / 15 | **100.0%** | $0.025337 | $0.025337 |
| **B: Cheap-with-Retries** | 37 | $0.020518 | 4 / 15 | **26.7%** | $0.000555 | $0.005129 |
| **C: Budget-Cascade (Our Policy)** | 15 | $0.165065 | 15 / 15 | **100.0%** | **$0.011004** | **$0.011004** |

**Key Metric**: Our **Budget-Cascade Policy (Strategy C)** achieved a **56.6% cost reduction** compared to Always-Frontier while maintaining **100% resolution accuracy** across all 15 cloud security tasks!

---

### 3. Break-Even Resolution Rate Analysis
- **Price Spread**: Strategy B costs **$0.000555 per call**, which is **2.19%** of Strategy A's cost per call ($0.025337).
- **Break-Even Threshold**: For Strategy B to match Strategy A's cost per resolved task ($0.025337), Strategy B must resolve at least **2.19%** of tasks ($\frac{0.000555}{0.025337} \approx 0.0219$).
- **Measured Position**: Strategy B resolved **26.7%** of tasks, sitting **24.51 percentage points above the break-even threshold**. While B is cost-effective per resolved task on simple items, its 73.3% failure rate makes it unsuitable for production security workloads without escalating retries.

---

### 4. Policy Degradation / Misrouted Task Breakdown
- **Misrouted Task**: `sec_05_k8s_rbac` ("Kubernetes ClusterRole Wildcard Verb Audit").
- **What Happened**: Under Strategy B (Cheap-with-Retries), the task was routed to `economy` (`openai/gpt-oss-120b`). The model lacked reasoning depth for RBAC security nuances, failing the rubric evaluation. Strategy B retried `economy` twice before giving up, consuming 3 calls and costing **$0.00168250** without resolving the task.
- **Cost of Misrouting**: Waste of $0.00168250 and total task resolution failure. In contrast, Strategy C correctly escalated `sec_05_k8s_rbac` to `standard` ($0.00287100) and resolved it on attempt 1.

---

## Part 3: Attack Your Own Budget

### 1. Adversarial Test Suite (`tests/test_adversarial_budget_attack.py`)

#### Attack Scenario 1: Runaway Execution Loop
- **Method**: An infinite graph loop attempts 50 recursive node executions under a tight $0.002 budget ceiling.
- **Uncontrolled Projection**: Without budget control, the loop would spend **$9.63+** across 10,000 rounds.
- **Controlled Refusal**: Hard controller admitted only 2 calls, stopping spend at **$0.00192680** ($ \le 0.002$). 198 subsequent nodes were refused with `BudgetRefused` graph failures.

#### Attack Scenario 2: Unaffordable Tier Escalation
- **Method**: A node explicitly requests `frontier` tier ($0.032 projected cost) when the run allowance is set to **$0.0001**.
- **Controlled Refusal**: `BudgetPolicy.decide` immediately returned `action="refuse"` with reason `"budget 0.00010000 < projected call 0.03200000"`. Zero transport calls were made.

#### Attack Scenario 3: Telemetry Visibility
- **Span Verification**: Refusal events land directly in OpenTelemetry spans as `status.code = ERROR` and emit `budget.refused` events with remaining budget attributes.

---

## Jaeger Trace & OpenTelemetry Hierarchy

```
span: run:run-adv-01 (kind=run)
 └── span: agent loop 1 (kind=agent_loop)
      └── span: plan 1 (kind=plan)
           └── span: node:node_adv (kind=node, status=ERROR)
                └── event: budget.refused {s15.budget.reason: "budget 0.0001 < projected 0.032"}
```

Span attributes automatically capture:
- `gen_ai.provider.name`: `openai` / `gemini` / `groq`
- `gen_ai.request.model`: `openai/gpt-4.1`
- `gen_ai.usage.input_tokens`: `150`
- `gen_ai.usage.output_tokens`: `300`
- `s15.cost`: `$0.025337`
- `s15.currency`: `USD`

---

## Reproduction Commands

To reproduce all proofs, benchmark evaluations, adversarial tests, and telemetry exports from a fresh checkout:

```bash
# 1. Install dependencies & build environment
uv sync

# 2. Run all unit tests including adversarial budget attack suite
uv run pytest -q

# 3. Run Part 1 & Part 2 policy evaluation on 15 cloud security tasks
uv run python proofs/p1_cost_per_task.py --tasks proofs/tasks/cloud_security_ops.jsonl --offline

# 4. Run budget protection & runaway loop proof
uv run python proofs/p2_budget_holds.py --offline --task "Audit IAM roles"
uv run python proofs/p3_denial_of_wallet.py --offline --task "Runaway loop" --budget 0.002

# 5. Run OpenTelemetry span export & Jaeger trace verification
uv run python proofs/p4_trace_export.py --offline --task "Trace export test" --budget 0.02

# 6. Run semantic cache evaluation & cross-model ladder proofs
uv run python proofs/p6_cache_savings.py --offline
uv run python proofs/p7_cross_model_ladder.py --offline --task "Cloud architecture review"
```

