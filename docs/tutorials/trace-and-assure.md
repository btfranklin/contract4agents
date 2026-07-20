# Capture and Assure a Run

This tutorial continues the [First Contract Project](first-contract-project.md)
through the final two Contract4Agents phases:

```text
Declare -> Compile -> Plan -> Materialize -> Run -> Trace -> Assure
```

The application still owns execution, retries, persistence, and recovery.
Contract4Agents captures portable evidence, proves which instrumentation paths
closed, assesses declared controls, and assembles the review bundle.

## What Each Artifact Proves

| Artifact | What it establishes |
| --- | --- |
| `NormalizedTrace` | What events were observed under one contract and plan |
| `TraceClosureEvidence` | Which attempts and instrumentation channels were completely captured |
| `TraceFrontier` | The exact ordered trace snapshot attested by the closure |
| `TraceCaptureSnapshot` | One internally consistent trace-plus-closure pair |
| `TraceClosureManifest` | Versioned closure evidence for every run in a trace artifact |
| `attempt.selected` | Which host-owned retry attempt governs logical-run output assurance |

Event occurrence and instrumentation closure answer different questions. Seeing
one tool event does not prove every tool path was captured; closure evidence is
what allows an absence or upper bound to support assurance.

## Register One Process Router

The OpenAI Agents SDK trace-processor registry is process-global. Register one
router when the process starts, then open a disposable session for each logical
run:

```python
from agents import add_trace_processor
from contract4agents.tracing import OpenAINormalizedTraceRouter

trace_router = OpenAINormalizedTraceRouter()
add_trace_processor(trace_router)
```

Do not register a router for every run.

## Capture One Attempt

The example below uses the `SupportResponder` from the first tutorial. Attempt
identity belongs to the host because the host decides whether and when to
retry.

```python
import asyncio
from pathlib import Path

from agents import Runner
from contract4agents import compile_project, materialize
from contract4agents.tracing import (
    TraceAttempt,
    TraceClosureManifest,
    write_trace_jsonl,
)


async def run_support_request() -> None:
    artifacts = compile_project("agent_contracts")
    system = materialize(
        "agent_contracts",
        target="openai",
        profile="development",
    )
    responder = system.agents["SupportResponder"]
    attempt = TraceAttempt(
        invocation_id="support:request-123",
        attempt_id="support:request-123:attempt-1",
        number=1,
    )
    session = trace_router.open_session(
        artifacts.ir,
        system.plan,
        run_id="support-run-123",
    )

    with session:
        with session.bind_attempt(attempt, agent="SupportResponder"):
            result = await Runner.run(
                responder,
                input="When will my order ship?",
            )
            session.record_result(
                result,
                agent="SupportResponder",
                attempt=attempt,
            )
        session.record_terminal_attempt(
            agent="SupportResponder",
            attempt=attempt,
            outcome="succeeded",
        )

    snapshot = session.closed_snapshot
    evidence_dir = Path(".contract/evidence/support-run-123")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    write_trace_jsonl(evidence_dir / "trace.jsonl", snapshot.trace)
    (evidence_dir / "trace-closure.json").write_text(
        TraceClosureManifest((snapshot.closure,)).to_json()
    )


if __name__ == "__main__":
    asyncio.run(run_support_request())
```

Every successful SDK result must pass through `record_result(...)`, including a
result with zero provider-hosted calls. That records a response-batch receipt.
If `Runner.run(...)` raises, call
`session.normalize_exception_responses(exception, ...)` before retrying or
reraising so retained raw responses do not disappear from the evidence.

For a retry, create a new `TraceAttempt` with the next number and `retry_of`
pointing to the prior attempt ID. Record exactly one terminal selection for the
invocation when the host has made that decision.

## Persist a Recovery Snapshot

Call `session.snapshot()` while a session remains open to obtain the same
trace-plus-closure type without closing capture:

```python
snapshot = session.snapshot()
```

Persist `snapshot.trace` and `snapshot.closure` as one application recovery
unit before advancing workflow state. Contract4Agents validates their exact
frontier when a later process supplies them as `prior_trace=` and
`prior_closure=`. It does not make those files and application state one
transaction; persistence ordering and crash policy remain host responsibilities.

## Assess Declared Controls

The public CLI reconstructs the reviewed plan and assesses the normalized
evidence locally:

```bash
contract4agents assess agent_contracts \
  --target openai \
  --profile development \
  --trace .contract/evidence/support-run-123/trace.jsonl \
  --trace-closure .contract/evidence/support-run-123/trace-closure.json
```

Observed violations fail assessment. Missing or insufficient evidence remains
`unverified`; it never becomes a pass merely because an event was absent.

## Assemble the Assurance Bundle

Run the deterministic eval from the first tutorial and record the provenance
for this review:

```bash
contract4agents eval agent_contracts \
  --target openai \
  --profile development \
  --out .contract/evidence/eval-results.json

printf '{"source":"support-service release review"}\n' \
  > .contract/evidence/provenance.json
```

Then assemble the declared, planned, observed, and assessed artifacts:

```bash
contract4agents assure agent_contracts \
  --target openai \
  --profile development \
  --trace .contract/evidence/support-run-123/trace.jsonl \
  --trace-closure .contract/evidence/support-run-123/trace-closure.json \
  --eval-results .contract/evidence/eval-results.json \
  --provenance .contract/evidence/provenance.json \
  --out .contract/assurance/support-run-123
```

Contracts declaring a `run_spec` also pass one versioned
`--run-spec-evidence` manifest covering every trace run. Run-spec selection and
stage evidence remain host-owned; the CLI computes the assessment rather than
accepting a caller-authored passing result.

The assurance bundle is portable review evidence for release review, incident
analysis, or compliance export. It is not a legal certification and does not
replace the host application's risk decision.
