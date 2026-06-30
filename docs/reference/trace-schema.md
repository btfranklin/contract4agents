# Trace Schema Reference

V1 normalized trace events include:

- `datasource.started`
- `datasource.resolved`
- `datasource.failed`
- `tool.requested`
- `tool.started`
- `tool.allowed`
- `tool.denied`
- `tool.completed`
- `tool.failed`
- `approval.requested`
- `approval.completed`
- `guardrail.rejected`
- `agent.started`
- `agent.handoff`
- `agent.completed`
- `llm.started`
- `llm.completed`

Provider-specific metadata may be attached to event data, but event type names should remain stable.
Semantic eval outcomes are reported by the eval runner; V1 does not emit a trace
event for skipped or completed semantic checks.
