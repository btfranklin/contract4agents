# Harness Research Example

This example defines a research coordinator and one evidence analyst. The
analyst reads a local synthetic record. The coordinator receives the analyst's
typed result through a declared delegation with no shared history.

The application owns tool dispatch and the fixed invocation sequence. The
contracts own instructions, grants, types, and composition. See the
[Harness target reference](../../docs/reference/agentcore-harness-adapter.md)
for supported mappings and assurance limits.

## Run Without AWS

From the repository root:

```bash
pdm run contract4agents check examples/harness-research
pdm run contract4agents harness-config examples/harness-research \
  --target harness --profile test --out .contract/harness
pdm run python examples/harness-research/run_demo.py
```

The demonstration uses a scripted synthetic AWS boundary. It performs deployment
verification, materialization, a pending delegation, one pending source lookup,
and typed continuation. It prints the source-backed result and writes events to
`.contract/harness-demo/trace.jsonl`. It calls no model or AWS service. It tests
transport and application integration rather than model behavior.

The source record is in `data/source.json`; the tool is `harness_sources.py`.
Each agent keeps its goal and guidance in its own file under `agents/`.
The binding contains an example role ARN; replace it for real deployment.

## Use Deployed AWS Resources

Install the optional client with `pdm install -G agentcore-harness`. Copy the
binding file to an application-owned location and select a real execution role
and supported Bedrock model in its `test` profile. Use that same file to generate
configuration and invoke the deployed agents:

```bash
pdm run contract4agents harness-config examples/harness-research \
  --target harness --profile test --bindings /path/to/harness.targets.toml \
  --out .contract/harness
```

Review the generated files. Your pipeline can submit each with
`aws bedrock-agentcore-control create-harness --cli-input-json file://PATH`.
It must also create or select stable endpoints. Contract4Agents performs no
provisioning. Keep endpoint versions stable after verification.

Create an application-owned JSON deployment map with `ResearchCoordinator` and
`EvidenceAnalyst` entries. Each entry contains `harness_arn` and `endpoint_name`.
Then explicitly select the live route:

```bash
pdm run python examples/harness-research/run_demo.py --live \
  --bindings /path/to/harness.targets.toml \
  --deployment-map /path/to/harness-deployments.json
```

The live route uses only synthetic data and incurs AWS model and session costs.
It permits one delegation, one source lookup, and their continuations. Extra
tool calls or failures stop the demonstration. It never retries or changes
model. Credentials use the normal SDK provider chain.

The demonstration creates no AWS resources. Your deployment system must stop
idle sessions and remove disposable Harness resources and endpoints when
finished. Verify the execution role and caller IAM policies. The sample
principal is synthetic; production applications must supply authenticated
identity. Missing managed-loop trace closure or external-effect evidence stays
unverified in assurance.
