"""Run the example only after the caller explicitly selects a live invocation."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pydantic import BaseModel
from strands import Agent

from contract4agents import materialize
from contract4agents.adapters.strands import extract_strands_output


async def run(request: str) -> None:
    system = materialize(Path(__file__).parent, target="strands", profile="test")
    agent = system.agents["SourceSummary"]
    if not isinstance(agent, Agent):
        raise TypeError("The selected target did not produce a Strands agent")
    result = await agent.invoke_async(request)
    value = extract_strands_output(system, agent, result)
    if not isinstance(value, BaseModel):
        raise TypeError("The result did not satisfy its generated output type")
    print(value.model_dump_json())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    parser.add_argument("--live", action="store_true", required=True)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.request))


if __name__ == "__main__":
    main()
