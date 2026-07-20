from contract4agents.assurance import BUNDLE_VERSION, RUN_SPEC_ASSESSMENT_INPUT_VERSION
from contract4agents.codegen import GENERATOR_VERSION
from contract4agents.eval_campaigns import EVAL_DATA_VERSION
from contract4agents.ir import IR_VERSION
from contract4agents.planning import PLAN_VERSION
from contract4agents.target_bindings import TARGET_BINDINGS_SCHEMA_VERSION
from contract4agents.tracing import TRACE_CLOSURE_MANIFEST_VERSION, TRACE_SCHEMA_VERSION
from contract4agents.visualization._graph import VISUALIZATION_VERSION


def test_pre_stable_format_versions_remain_one() -> None:
    assert {
        BUNDLE_VERSION,
        EVAL_DATA_VERSION,
        GENERATOR_VERSION,
        IR_VERSION,
        PLAN_VERSION,
        RUN_SPEC_ASSESSMENT_INPUT_VERSION,
        TARGET_BINDINGS_SCHEMA_VERSION,
        TRACE_CLOSURE_MANIFEST_VERSION,
        TRACE_SCHEMA_VERSION,
        VISUALIZATION_VERSION,
    } == {"1"}
