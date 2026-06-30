# Incident Command Walkthrough

`examples/incident-command/` is the first public V1 example and the best entry
point for understanding the project from source files.

Start with `../../examples/incident-command/README.md` for the beginner-facing
tour of the files and generated artifacts.

The example uses local Python fake tools and a seeded SQLite database to simulate
incident investigation without MCP servers, remote APIs, or live credentials.

Run:

```bash
pdm run python examples/incident-command/data/seed.py
pdm run contract4agents check examples/incident-command
pdm run contract4agents compile examples/incident-command --out .contract/build
pdm run pytest tests/integration/test_incident_command.py
```

The hidden truth says deploy `8f31c2` changed payment timeout handling and caused
checkout payment timeouts. Agents must discover that through fake log, deploy,
and metrics tools.

The source files under `examples/incident-command/` are what a user would write.
The generated files under `.contract/build` are review and adapter artifacts.
They are not source and can be deleted and regenerated.
