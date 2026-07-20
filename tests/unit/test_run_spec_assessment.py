from __future__ import annotations

import json
from dataclasses import replace

import pytest

from contract4agents.assurance import (
    RunSpecAssessmentInput,
    RunSpecAssessmentManifest,
    RunSpecEvidence,
    RunSpecSelection,
    RunSpecStageObservation,
    assemble_assurance_bundle,
    assess_run_spec,
)
from contract4agents.ir import (
    AgentIR,
    CanonicalIR,
    FrozenMap,
    RunSpecDerivedValueIR,
    RunSpecIR,
    RunSpecStageIR,
    TypeFieldIR,
    TypeIR,
    contract_digest,
    parse_type_ref,
    semantic_id,
)
from contract4agents.planning import AdapterPlan, AgentPlan, MaterializationPlan
from contract4agents.tracing import (
    NormalizedTrace,
    ProviderCorrelation,
    TraceAttempt,
    TraceAttemptClosure,
    TraceClosureEvidence,
    TraceEvent,
    TraceFrontier,
    TraceRunContext,
    TraceSemanticRefs,
    dumps_trace_jsonl,
)
from contract4agents.visualization import build_visualization_graph


def test_run_spec_assessment_passes_complete_typed_stage_and_assertion_evidence() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)
    evidence = _evidence(ir)

    result = assess_run_spec(ir, plan, trace, "ResearchRun", evidence)

    assert result.status == "passed"
    assert [item.status for item in result.stages] == ["passed", "passed"]
    assert [item.status for item in result.assertions] == ["passed", "passed"]
    assert result.run_spec_id == "run_spec:ResearchRun"
    assert result.to_json() == result.to_json()
    node = next(item for item in build_visualization_graph(ir)["nodes"] if item["id"] == "run_spec:ResearchRun")
    assert node["truth"]["declared"]["derived_values"] == [
        {"name": "cited_ids", "type": "list[string]"},
        {"name": "allowed_ids", "type": "list[string]"},
    ]


def test_run_spec_assessment_never_treats_missing_stage_evidence_as_control_success() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)
    evidence = replace(
        _evidence(ir),
        stage_observations=_evidence(ir).stage_observations[:1],
    )

    result = assess_run_spec(ir, plan, trace, "ResearchRun", evidence)

    assert result.status == "violated"
    assert next(item for item in result.stages if item.stage == "synthesis").status == "violated"


def test_run_spec_assessment_requires_explicit_workflow_and_trace_evidence() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)
    incomplete = RunSpecEvidence(
        "unverified",
        "The workflow recorder did not close the run.",
        _evidence(ir).stage_observations[:1],
    )

    workflow_result = assess_run_spec(ir, plan, trace, "ResearchRun", incomplete)
    trace_result = assess_run_spec(
        ir,
        replace(plan, expected_event_types=("agent.started", "workflow.closed")),
        _trace(ir, replace(plan, expected_event_types=("agent.started", "workflow.closed"))),
        "ResearchRun",
        _evidence(ir),
    )

    assert workflow_result.status == "unverified"
    assert all(item.status == "unverified" for item in workflow_result.stages)
    assert trace_result.status == "passed"
    assert [item.status for item in trace_result.assertions] == ["passed", "passed"]


def test_run_spec_assessment_rejects_wrong_agent_schema_cardinality_and_extra_stage() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)
    observations = (
        RunSpecStageObservation(
            "plan-1",
            "plan",
            semantic_id("agent", "Writer"),
            {"topic": "evidence"},
            ("evt-plan",),
        ),
        RunSpecStageObservation(
            "synthesis-1",
            "synthesis",
            semantic_id("agent", "Writer"),
            {"answer": 42},
            ("evt-write",),
        ),
        RunSpecStageObservation(
            "synthesis-2",
            "synthesis",
            semantic_id("agent", "Writer"),
            {"answer": "duplicate"},
            ("evt-write",),
        ),
        RunSpecStageObservation(
            "extra-1",
            "undeclared",
            semantic_id("agent", "Writer"),
            {"answer": "extra"},
            evidence_refs=("artifact:extra",),
        ),
    )
    evidence = RunSpecEvidence(
        "complete",
        "The workflow ledger is closed.",
        observations,
        FrozenMap({"cited_ids": ("a",), "allowed_ids": ("a",)}),
        ("workflow-ledger:run-1",),
    )

    result = assess_run_spec(ir, plan, trace, "ResearchRun", evidence)

    assert result.status == "violated"
    assert result.unexpected_observation_ids == ("extra-1",)
    assert {item.stage: item.status for item in result.stages} == {
        "plan": "violated",
        "synthesis": "violated",
    }


def test_run_spec_assessment_validates_output_schema_and_linked_trace_evidence() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)
    base = _evidence(ir)
    bad_output = replace(
        base.stage_observations[1],
        output={"answer": 42},
    )
    missing_event = replace(
        base.stage_observations[1],
        evidence_event_ids=("evt-missing",),
    )

    schema_result = assess_run_spec(
        ir,
        plan,
        trace,
        semantic_id("run_spec", "ResearchRun"),
        replace(base, stage_observations=(base.stage_observations[0], bad_output)),
    )
    evidence_result = assess_run_spec(
        ir,
        plan,
        trace,
        "run_spec:ResearchRun",
        replace(base, stage_observations=(base.stage_observations[0], missing_event)),
    )

    assert schema_result.status == "violated"
    assert "does not conform" in schema_result.stages[1].reason
    assert evidence_result.status == "unverified"
    assert "missing trace events" in evidence_result.stages[1].reason


def test_trace_backed_stage_observation_requires_linked_agent_identity() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = NormalizedTrace(
        tuple(replace(event, semantic=TraceSemanticRefs()) for event in _trace(ir, plan).events)
    )

    result = assess_run_spec(ir, plan, trace, "ResearchRun", _evidence(ir))

    assert all(item.status == "unverified" for item in result.stages)
    assert all("no linked event with agent identity" in item.reason for item in result.stages)


def test_run_spec_output_schema_enforces_datetime_format() -> None:
    base = _ir()
    brief_id = semantic_id("type", "Brief")
    brief = base.types[brief_id]
    assert isinstance(brief, TypeIR)
    invalid_datetime_ir = replace(
        base,
        types=FrozenMap(
            (
                identifier,
                replace(
                    type_def,
                    fields=(TypeFieldIR("answer", parse_type_ref("datetime")),),
                )
                if identifier == brief_id
                else type_def,
            )
            for identifier, type_def in base.types.items()
        ),
    )
    plan = _plan(invalid_datetime_ir)

    result = assess_run_spec(
        invalid_datetime_ir,
        plan,
        _trace(invalid_datetime_ir, plan),
        "ResearchRun",
        _evidence(invalid_datetime_ir),
    )

    assert result.status == "violated"
    assert "not a 'date-time'" in result.stages[1].reason


def test_run_spec_stage_cardinality_supports_optional_absence_and_many_outputs() -> None:
    base = _ir()
    declaration = next(iter(base.run_specs.values()))
    stages = (
        replace(declaration.stages[0], cardinality="optional"),
        replace(declaration.stages[1], cardinality="many"),
    )
    ir = replace(base, run_specs=FrozenMap({declaration.id: replace(declaration, stages=stages)}))
    plan = _plan(ir)
    trace = _trace(ir, plan)
    writer = _evidence(ir).stage_observations[1]
    repeated = replace(writer, observation_id="synthesis-2")
    evidence = replace(
        _evidence(ir),
        stage_observations=(writer, repeated),
    )

    result = assess_run_spec(ir, plan, trace, "ResearchRun", evidence)

    assert result.status == "passed"
    assert {item.stage: item.status for item in result.stages} == {
        "plan": "passed",
        "synthesis": "passed",
    }


def test_run_spec_assessment_rejects_an_unknown_declaration() -> None:
    ir = _ir()
    plan = _plan(ir)

    with pytest.raises(ValueError, match="does not declare run spec"):
        assess_run_spec(ir, plan, _trace(ir, plan), "MissingRun", _evidence(ir))


def test_run_spec_results_are_distinct_assurance_bundle_evidence() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)
    result = assess_run_spec(ir, plan, trace, "ResearchRun", _evidence(ir))
    common = {
        "normalized_trace_jsonl": dumps_trace_jsonl(trace),
        "trace_closures": (_closure(trace),),
        "control_results": (),
        "eval_results": {"campaigns": []},
        "provenance": {"sources": ["test"]},
    }

    missing = assemble_assurance_bundle(ir, plan, **common)
    explicitly_missing = assemble_assurance_bundle(
        ir,
        plan,
        run_spec_results=(),
        run_spec_selections=(),
        **common,
    )
    selection = RunSpecSelection(
        "run-1",
        result.run_spec_id,
        "The host selected the research workflow.",
        ("workflow-ledger:run-1",),
    )
    complete = assemble_assurance_bundle(
        ir,
        plan,
        run_spec_results=(result,),
        run_spec_selections=(selection,),
        **common,
    )

    assert "BUNDLE013" in {item.code for item in missing.diagnostics}
    assert "BUNDLE013" in {item.code for item in explicitly_missing.diagnostics}
    assert '"status": "unverified"' in missing.files["run-spec-results.json"]
    assert complete.complete
    assert complete.bundle_version == "1"
    assert '"run_spec_id": "run_spec:ResearchRun"' in complete.files["run-spec-results.json"]
    assert result.evidence_digest in complete.files["run-spec-results.json"]

    with pytest.raises(ValueError, match="does not match the bundle contract and plan"):
        assemble_assurance_bundle(
            ir,
            plan,
            run_spec_results=(replace(result, plan_digest=f"sha256:{'f' * 64}"),),
            run_spec_selections=(selection,),
            **common,
        )


def test_assurance_bundle_accepts_explicit_no_applicable_run_spec() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)

    bundle = assemble_assurance_bundle(
        ir,
        plan,
        normalized_trace_jsonl=dumps_trace_jsonl(trace),
        trace_closures=(_closure(trace),),
        control_results=(),
        eval_results={"campaigns": []},
        provenance={"sources": ["test"]},
        run_spec_selections=(
            RunSpecSelection(
                "run-1",
                None,
                "This invocation did not use a declared workflow.",
                ("workflow-ledger:run-1",),
            ),
        ),
    )

    assert bundle.complete


def test_assurance_bundle_requires_selection_for_each_trace_run() -> None:
    ir = _ir()
    plan = _plan(ir)
    trace = _trace(ir, plan)
    second_run = NormalizedTrace(
        tuple(
            replace(
                event,
                context=replace(event.context, run_id="run-2"),
                event_id=f"run-2:{event.event_id}",
                parent_event_id=(
                    f"run-2:{event.parent_event_id}"
                    if event.parent_event_id is not None
                    else None
                ),
            )
            for event in trace.events
        )
    )
    combined_trace = NormalizedTrace(trace.events + second_run.events)
    common = {
        "normalized_trace_jsonl": dumps_trace_jsonl(combined_trace),
        "control_results": (),
        "eval_results": {"campaigns": []},
        "provenance": {"sources": ["test"]},
    }

    empty = assemble_assurance_bundle(ir, plan, run_spec_selections=(), **common)
    unrelated = assemble_assurance_bundle(
        ir,
        plan,
        run_spec_selections=(
            RunSpecSelection(
                "other-run",
                None,
                "This invocation did not use a declared workflow.",
                ("workflow-ledger:other-run",),
            ),
        ),
        **common,
    )
    partial = assemble_assurance_bundle(
        ir,
        plan,
        run_spec_selections=(
            RunSpecSelection(
                "run-1",
                None,
                "This invocation did not use a declared workflow.",
                ("workflow-ledger:run-1",),
            ),
        ),
        **common,
    )

    for bundle in (empty, unrelated, partial):
        assert not bundle.complete
        assert "BUNDLE013" in {item.code for item in bundle.diagnostics}
        assert '"status": "unverified"' in bundle.files["run-spec-results.json"]


def test_complete_run_spec_evidence_requires_a_workflow_completeness_reference() -> None:
    with pytest.raises(ValueError, match="completeness evidence reference"):
        RunSpecEvidence("complete", "The host says it finished.")


def test_run_spec_assessment_manifest_round_trips_raw_evidence_and_rejects_mismatches() -> None:
    selection = RunSpecSelection(
        "run-1",
        "run_spec:ResearchRun",
        "The workflow ledger selected the declared run spec.",
        ("workflow-ledger:selection",),
    )
    manifest = RunSpecAssessmentManifest((RunSpecAssessmentInput(selection, _evidence(_ir())),))

    assert RunSpecAssessmentManifest.from_json(json.dumps(manifest.to_dict())) == manifest
    with pytest.raises(ValueError, match="requires assessment evidence"):
        RunSpecAssessmentInput(selection, None)
    with pytest.raises(ValueError, match="cannot carry assessment evidence"):
        RunSpecAssessmentInput(
            RunSpecSelection(
                "run-1",
                None,
                "No declared workflow applied.",
                ("workflow-ledger:none",),
            ),
            _evidence(_ir()),
        )


def _ir() -> CanonicalIR:
    plan_type = TypeIR(
        semantic_id("type", "Plan"),
        "Plan",
        (TypeFieldIR("topic", parse_type_ref("string")),),
    )
    brief_type = TypeIR(
        semantic_id("type", "Brief"),
        "Brief",
        (TypeFieldIR("answer", parse_type_ref("string")),),
    )
    planner = AgentIR(
        semantic_id("agent", "Planner"),
        "Planner",
        (),
        parse_type_ref("Plan"),
        "Plan the work.",
    )
    writer = AgentIR(
        semantic_id("agent", "Writer"),
        "Writer",
        (),
        parse_type_ref("Brief"),
        "Write the result.",
    )
    run_spec = RunSpecIR(
        semantic_id("run_spec", "ResearchRun"),
        "ResearchRun",
        (
            RunSpecStageIR("plan", planner.id, parse_type_ref("Plan")),
            RunSpecStageIR("synthesis", writer.id, parse_type_ref("Brief")),
        ),
        (
            RunSpecDerivedValueIR("cited_ids", "list[string]"),
            RunSpecDerivedValueIR("allowed_ids", "list[string]"),
        ),
        (
            "expect(trace.called_before(Planner, Writer))",
            "expect(value.cited_ids subset_of value.allowed_ids)",
        ),
    )
    return CanonicalIR.create(types=(plan_type, brief_type), agents=(planner, writer), run_specs=(run_spec,))


def _plan(ir: CanonicalIR) -> MaterializationPlan:
    return MaterializationPlan(
        contract_digest(ir),
        "test",
        "test",
        AdapterPlan("test", "1"),
        FrozenMap(
            (
                agent.id,
                AgentPlan(agent.id, agent.name, "test-model", FrozenMap(), agent.output_type),
            )
            for agent in ir.agents.values()
        ),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        FrozenMap(),
        (),
        ("agent.started",),
    )


def _trace(ir: CanonicalIR, plan: MaterializationPlan) -> NormalizedTrace:
    context = TraceRunContext("run-1", "thread-1", contract_digest(ir), plan.plan_digest)
    planner_attempt = TraceAttempt("planner:1", "planner-attempt-1", 1)
    writer_attempt = TraceAttempt("writer:1", "writer-attempt-1", 1)
    return NormalizedTrace(
        (
            TraceEvent(
                context,
                "evt-plan",
                None,
                "agent.started",
                1,
                TraceSemanticRefs(agent_id=semantic_id("agent", "Planner")),
                data={"attempt": planner_attempt.to_dict()},
                provider=ProviderCorrelation("test"),
            ),
            TraceEvent(
                context,
                "evt-write",
                None,
                "agent.started",
                2,
                TraceSemanticRefs(agent_id=semantic_id("agent", "Writer")),
                data={"attempt": writer_attempt.to_dict()},
                provider=ProviderCorrelation("test"),
            ),
        )
    )


def _closure(trace: NormalizedTrace) -> TraceClosureEvidence:
    attempts = {
        TraceAttempt.from_dict(event.data["attempt"]): event.semantic.agent_id
        for event in trace.events
    }
    return TraceClosureEvidence(
        context=trace.events[0].context,
        status="complete",
        reason="The workflow fixture covers every attempt.",
        frontier=TraceFrontier.from_trace(trace),
        channels=("agent",),
        attempts=tuple(
            TraceAttemptClosure(
                attempt,
                agent_id,
                "complete",
                "complete",
                evidence_refs=(f"fixture:{attempt.attempt_id}",),
            )
            for attempt, agent_id in attempts.items()
            if agent_id is not None
        ),
        evidence_refs=(f"fixture:{trace.run_ids[0]}:closure",),
    )


def _evidence(ir: CanonicalIR) -> RunSpecEvidence:
    return RunSpecEvidence(
        "complete",
        "The host workflow ledger is closed.",
        (
            RunSpecStageObservation(
                "plan-1",
                "plan",
                semantic_id("agent", "Planner"),
                {"topic": "evidence"},
                ("evt-plan",),
            ),
            RunSpecStageObservation(
                "synthesis-1",
                "synthesis",
                semantic_id("agent", "Writer"),
                {"answer": "done"},
                ("evt-write",),
            ),
        ),
        FrozenMap({"cited_ids": ("a",), "allowed_ids": ("a", "b")}),
        ("workflow-ledger:run-1",),
    )
