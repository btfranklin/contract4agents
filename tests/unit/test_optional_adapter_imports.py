from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def test_core_planning_is_lazy_and_missing_adapter_extras_are_actionable() -> None:
    script = dedent(
        """
        import sys

        class BlockOptionalSDKs:
            def find_spec(self, fullname, path=None, target=None):
                if (
                    fullname == "strands"
                    or fullname.startswith("strands.")
                    or fullname == "google.adk"
                    or fullname.startswith("google.adk.")
                    or fullname == "google.genai"
                    or fullname.startswith("google.genai.")
                ):
                    raise ModuleNotFoundError(fullname)
                return None

        sys.meta_path.insert(0, BlockOptionalSDKs())

        import contract4agents
        from pydantic import create_model
        from contract4agents.adapters._registry import get_adapter_registration
        from contract4agents.materialization import MaterializationError
        from contract4agents.materialization._google_adk import ADKSDK
        from contract4agents.materialization._strands import StrandsAgentsSDK

        for adapter in ("strands", "google_adk"):
            registration = get_adapter_registration(adapter)
            assert registration is not None
            assert registration.planner_capabilities().adapter == adapter

        try:
            StrandsAgentsSDK().create_model(
                model="test-model",
                model_options={},
                factory=None,
            )
        except MaterializationError as exc:
            assert "contract4agents[strands]" in str(exc)
        else:
            raise AssertionError("missing Strands extra did not fail")

        output_type = create_model("Output", value=(str, ...))
        try:
            ADKSDK().create_agent(
                semantic_name="Agent",
                native_name="c4a_agent_agent_deadbeef",
                description="",
                instructions="Return output.",
                model="gemini-2.5-flash",
                model_options={},
                model_factory=None,
                input_type=None,
                output_type=output_type,
                output_mode="native",
                tools=(),
            )
        except MaterializationError as exc:
            assert "contract4agents[google-adk]" in str(exc)
        else:
            raise AssertionError("missing Google ADK extra did not fail")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
