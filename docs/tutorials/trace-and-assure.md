# Capture and Assure a Run

This optional tutorial adds trace capture and a review bundle to the
[First Contract Project](first-contract-project.md). Use it when you want to
compare a run with declared expectations. You do not need trace capture to
construct and use an agent.

You will save three files: the generated agent's configuration checks, the
observed events, and a record of capture coverage called *closure evidence*.
Coverage matters because a missing event can mean either that an action did
not occur or that it was not recorded.

The example makes a live OpenAI request. It requires the first tutorial's
project and API key. The application continues to own execution and retries.

## Register One Process Router

The OpenAI Agents SDK trace-processor registry is process-global. Register one
router when the process starts, then open a disposable session for each logical
run:

Create `your_app/trace_support.py` with this setup:

```python
from agents import add_trace_processor
from contract4agents.tracing import OpenAINormalizedTraceRouter

trace_router = OpenAINormalizedTraceRouter()
add_trace_processor(trace_router)
```

Do not register a router for every run.

## Capture One Attempt

Place this code after the router setup in the same Python module. It uses the
`SupportResponder` from the first tutorial. The fixed request and attempt IDs
are for this example; use distinct IDs for separate requests in an application.

```python
import asyncio
from pathlib import Path

from agents import Runner
from contract4agents import materialize
from contract4agents.tracing import (
    TraceAttempt,
    TraceClosureManifest,
    write_trace_jsonl,
)


async def run_support_request() -> None:
    system = materialize(
        "your_app/agent_contracts",
        target="openai",
        profile="development",
    )
    artifacts = system.artifacts
    responder = system.agents["SupportResponder"]
    attempt = TraceAttempt(
        invocation_id="support:request-123",
        attempt_id="support:request-123:attempt-1",
        number=1,
    )
    session = trace_router.open_session(
        system,
        run_id="support-run-123",
    )

    with session:
        with session.bind_attempt(attempt, agent=responder):
            result = await Runner.run(
                responder,
                input=system.serialize_input_for_agent(
                    responder,
                    {"question": "When will my order ship?"},
                ),
            )
            session.record_result(result)
        session.record_terminal_attempt(
            attempt=attempt,
            outcome="succeeded",
        )

    snapshot = session.closed_snapshot
    evidence_dir = Path(".contract/evidence/support-run-123")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "materialization-conformance.json").write_text(
        system.validation.to_json()
    )
    write_trace_jsonl(evidence_dir / "trace.jsonl", snapshot.trace)
    (evidence_dir / "trace-closure.json").write_text(
        TraceClosureManifest((snapshot.closure,)).to_json()
    )


if __name__ == "__main__":
    asyncio.run(run_support_request())
```

From the project root, run the capture module before the assessment commands:

```bash
pdm run python -m your_app.trace_support
```

Every successful SDK result must pass through `record_result(...)`, including a
result with zero provider-hosted calls. That records a response-batch receipt.
If `Runner.run(...)` raises, call
`session.normalize_exception_responses(exception, ...)` before retrying or
reraising so retained raw responses do not disappear from the evidence.

For a retry, create a new `TraceAttempt` with the next number and `retry_of`
pointing to the prior attempt ID. Record exactly one terminal selection for the
invocation when the host has made that decision.

## Optional: Persist a Recovery Snapshot

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
pdm run contract4agents assess your_app/agent_contracts \
  --target openai \
  --profile development \
  --trace .contract/evidence/support-run-123/trace.jsonl \
  --trace-closure .contract/evidence/support-run-123/trace-closure.json
```

Observed violations fail assessment. Missing or insufficient evidence remains
`unverified`; it never becomes a pass merely because an event was absent.

## Assemble the Assurance Bundle

Complete the optional eval setup in step 8 of the first tutorial before this
step. Replay that supplied evidence and record the provenance for this review:

```bash
pdm run contract4agents eval replay your_app/agent_contracts \
  --target openai \
  --profile development \
  --out .contract/evidence/eval-replay.json

printf '{"source":"support-service release review"}\n' \
  > .contract/evidence/provenance.json
```

Then assemble the declared, planned, observed, and assessed artifacts:

```bash
pdm run contract4agents assure your_app/agent_contracts \
  --target openai \
  --profile development \
  --materialization-evidence .contract/evidence/support-run-123/materialization-conformance.json \
  --trace .contract/evidence/support-run-123/trace.jsonl \
  --trace-closure .contract/evidence/support-run-123/trace-closure.json \
  --eval-results .contract/evidence/eval-replay.json \
  --provenance .contract/evidence/provenance.json \
  --out .contract/assurance/support-run-123
```

Contracts declaring a `run_spec` also pass one versioned
`--run-spec-evidence` manifest covering every trace run. Run-spec selection and
stage evidence remain host-owned; the CLI computes the assessment rather than
accepting a caller-authored passing result.

The materialization evidence must come from the same contract, plan, adapter,
and adapter version as the assurance bundle. Missing or incomplete schema
coverage makes the bundle unverified.

The assurance bundle is portable review evidence for release review, incident
analysis, or compliance export. It is not a legal certification and does not
replace the host application's risk decision.
