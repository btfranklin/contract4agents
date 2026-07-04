# Capability Registry Reference

`contract4agents.registry.json` is an optional source-owned project file. It
declares the host-code surfaces that a contract project expects a host
application to provide.

Plain `contract4agents check` ignores a missing registry. If a registry exists,
the command validates the file shape without importing host code or requiring
complete coverage. `contract4agents check ROOT --strict-drift` requires a valid
registry and verifies declared contract capabilities against it.

## File Shape

The default path is `ROOT/contract4agents.registry.json`. Use
`--registry PATH` to point at another file. Relative registry paths are resolved
from `ROOT`.

```json
{
  "version": 2,
  "tools": {
    "crm.create_note": {
      "python": "app.tools.crm:create_note",
      "permissions": {
        "SupportCoordinator": "requires_approval"
      }
    },
    "billing.external_adjustment": {
      "external": true,
      "permissions": {
        "BillingAgent": "requires_approval",
        "SupportCoordinator": "requires_approval"
      }
    }
  },
  "hosted_tools": {
    "openai.web_search": {
      "provider": "openai",
      "tool": "web_search",
      "config": {"context_size": "medium"},
      "permissions": {
        "SupportCoordinator": "available"
      }
    }
  },
  "agents": {
    "SupportCoordinator": {
      "name": "SupportCoordinator",
      "factory": "app.agents:build_support_coordinator"
    }
  },
  "output_types": {
    "SupportReply": {
      "python": "app.models:SupportReply"
    }
  },
  "prompts": {
    "SupportCoordinator": {
      "path": "prompts/support_coordinator.md"
    }
  },
  "host_context": {
    "SupportCoordinator": ["AccountStatus"]
  }
}
```

Every section is a map. Empty section maps are valid. Tool and hosted-tool
entries use agent-scoped `permissions` maps because the same capability can be
declared by different agents with different permission states.

## Strict Drift Checks

`--strict-drift` checks these surfaces:

- every declared host Python tool has a registry entry;
- local tool refs import and point at callables;
- explicitly host-owned tools use `external: true`;
- every declared agent/tool permission pair has a matching registry permission;
- stale tool, hosted-tool, agent, and per-agent permission entries are rejected;
- hosted-tool provider, tool, config, and permissions match the contract;
- registered agent names match contract agent names, and factory refs import;
- registered output types import Pydantic v2 `BaseModel` classes whose required
  fields and property schemas match the contract schema;
- registered prompt assets exist and map to known agents;
- every manifest `host_context` type is marked in the registry.

The checker imports only refs explicitly named in the registry. It does not call
tool functions, SDK factories, hosted-tool factories, or business workflows.

## Diagnostics

Capability registry diagnostics use the `CAP###` range:

- `CAP001`: invalid registry shape or JSON;
- `CAP002`: missing registry when one is required or explicitly requested;
- `CAP010`: missing strict registry entry;
- `CAP020`: Python import path could not be resolved;
- `CAP021`: imported callable/factory ref is not callable;
- `CAP030`: permission mismatch;
- `CAP040`: agent name drift;
- `CAP050`: output-type drift;
- `CAP060`: hosted-tool drift;
- `CAP070`: prompt asset drift;
- `CAP080`: host-context marker drift;
- `CAP090`: stale registry entry that no contract declaration uses.
