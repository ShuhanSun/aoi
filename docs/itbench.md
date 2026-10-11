# ITBench SRE mitigation

AOI can run against a locally deployed ITBench SRE scenario. ITBench owns
deployment, fault injection, restricted agent access, and grading. This adapter
reuses AOI's Observer, Probe, Executor, and Compressor. It does not treat an
AOI `submit()` call as a passing ITBench verdict.

## Validated local deployment

The integration was validated against ITBench commit `333afa05` on Kubernetes
1.34.0. Reproducible environment fixes are in
`benchmarks/itbench/patches/itbench-333afa05.patch`: scenario application enablement,
failed-injection propagation, missing-object validation, pinned telemetry image
and configuration corrections, and Jaeger database initialization.

For a clean checkout at that revision:

```sh
git -C /absolute/path/to/ITBench apply --check --unidiff-zero /absolute/path/to/aoi/benchmarks/itbench/patches/itbench-333afa05.patch
git -C /absolute/path/to/ITBench apply --unidiff-zero /absolute/path/to/aoi/benchmarks/itbench/patches/itbench-333afa05.patch
```

The existing local checkout already has these edits. Other revisions require
reviewing the patch first. Ansible was run with ITBench's Python 3.13 environment
and uv 0.12.24. The isolated Kind cluster uses MetalLB for gateway IPs;
Prometheus is exposed to the host with `kubectl port-forward`.

## Prepare ITBench

Follow the upstream [ITBench SRE scenario guide](https://github.com/itbench-hub/ITBench/blob/main/documentation/getting-started/scenarios.md).
Use a separate ITBench checkout, install its own dependencies, configure its
group variables, and start one SRE scenario. For example, from that checkout:

```sh
cd scenarios/sre
SCENARIO_NUMBER=1 make start-scenario
SCENARIO_NUMBER=1 make enable-agent-access
```

Find the restricted agent kubeconfig produced by ITBench's agent-access step.
The path depends on ITBench's `storage.local.directory` setting. Use that file,
not your personal `~/.kube/config`. Obtain the incident text and optional
Prometheus URL from the running scenario. Keep the scenario running until its
external evaluation is complete.

## Run AOI

From this AOI checkout:

```sh
.venv/bin/python -m benchmarks.itbench.run \
  --itbench-dir /absolute/path/to/ITBench \
  --scenario-number 1 \
  --incident 'Investigate and resolve the active SRE incident' \
  --kubeconfig /absolute/path/to/restricted-kubeconfig \
  --prometheus-url http://prometheus.example:9090
```

`--prometheus-url` is optional. If supplied, AOI may use `get_alerts()` and
`query_prometheus('PromQL')`. Kubernetes commands use
`exec_shell('kubectl ...')`; the adapter injects the restricted kubeconfig.
The runner uses `gpt-4o-mini` by default; use `--model` only to override it.
Probe rejects state-changing kubectl verbs. The scenario can also be started
with `--start-scenario`, which calls ITBench's `start-scenario` and
`enable-agent-access` Make targets after its checkout has been configured.
Use `--stop-scenario` only when the run and external evaluation are finished.

Agents should prefer `exec_kubectl(['get', 'pods', '-n', 'otel-demo'])` over
nested shell strings. JSON patches are one argument in the list. For a
manifest, use `exec_kubectl(['apply', '-n', 'otel-demo', '-f', '-'], stdin='...')`.
Shell operators, interactive `kubectl edit`, remote shell execution, and
connection overrides are rejected. Submission requires a successful mutation
followed by a successful read; this gate checks tool execution, not benchmark
success. Rejected submissions are fed back to the Observer.

Each attempt writes `res/itbench/sre-<number>-<timestamp>.json` with the
task ID, ITBench revision, model and budgets, tool calls with outputs,
Observer/Probe/Executor/Compressor token usage, and elapsed time. Pass
`--input-usd-per-million` and `--output-usd-per-million` to record an estimated
USD cost for that model. The total uses provider-reported usage when AWorld
supplies it; the record also keeps per-call usage. Without usage or prices,
`cost_usd` is `null`, never an assumed zero.

The `success` field remains `null` until an external ITBench verifier provides
a JSON object containing a boolean `success` or `passed`. Supply its path with
`--verdict-json` if it already exists. If grading finishes later, attach it
without rerunning the agent:

```sh
.venv/bin/python -m benchmarks.itbench.verdict \
  res/itbench/sre-1-<timestamp>.json /absolute/path/to/itbench-verdict.json
```

AOI's submission and local command output are never used as an official
pass/fail score. Do not compare unverified attempts with published ITBench
success rates.

For archived scenario 1, `--local-verify` additionally records whether the
homepage flood flag is disabled and `flagd` / `load-generator` are running with
at least one ready replica at the current generation. It records
`local_verification`, with `official: false`, and leaves `success` unchanged.
It does not establish full symptom recovery or an official score. Run it independently:

```sh
.venv/bin/python -m benchmarks.itbench.verify \
  --kubeconfig /absolute/path/to/restricted-kubeconfig \
  --output res/itbench/runtime/local-check.json
```

Local check JSON is rejected by the official verdict importer. The public
[ITBench-Evaluations](https://github.com/itbench-hub/ITBench-Evaluations) toolkit
scores SRE RCA outputs with an LLM judge; its RCA scores are not a live
mitigation pass/fail verdict for this archived scenario.

After grading, summarize all local attempts with:

```sh
.venv/bin/python -m benchmarks.itbench.summary res/itbench --output res/itbench/summary.json
```

The summary counts unverified attempts separately and computes success rate
only over externally graded attempts.

## Full library SRE suite

`benchmarks.itbench.batch` selects all SRE scenarios in the pinned
`library/scenarios` catalog (31 in the validated checkout). The current upstream
Make/Ansible injector uses `archive/scenarios`; the batch checks matching fault
IDs and Kubernetes targets, and saves both definitions' hashes. Fault metadata
and ground truth are not included in agent incidents.

Each scenario uses fresh application namespaces on `kind-dev`. The runner
checks the admin context, node names, and ITBench inventory before cleanup.
Model settings are fixed to gpt-4o-mini, temperature 0, and 14 iterations.
Deployment, injection, model, and cleanup logs are saved separately. Setup
failures remain in the suite ledger rather than becoming model failures.

```sh
.venv/bin/python -m benchmarks.itbench.batch \
  --itbench-dir /absolute/path/to/ITBench \
  --admin-kubeconfig res/itbench/runtime/kind-dev-admin.yaml \
  --itbench-python /absolute/path/to/ITBench/.venv/bin/python \
  --docker-config /absolute/path/to/docker-config \
  --output res/itbench/full-library-gpt-4o-mini
```

You can set `UV_PYTHON` and `DOCKER_CONFIG` instead of passing those two options.

Resume with the same command and output directory. Completed cases are skipped;
`--retry-environment-errors` retries setup failures while preserving their earlier
ledgers. `progress.json` records the active scenario/phase, while `summary.json`
and `report.md` track finished cases. Completion or a blocked batch produces
`results.zip`, excluding credentials. Each model attempt retains full task-level
configuration, tools, provider usage, timing, and estimated cost.

NetworkPolicy faults need enforcement beyond Kind's default CNI. The tested
[policy-only kube-router configuration](../benchmarks/itbench/manifests/kube-router-policy.yaml)
keeps the existing CNI and kube-proxy. Its temporary-pod integration test confirmed
allow → deny → allow. The upstream scenario-31 injector is patched to use
`ingress: []`; an ingress rule with an empty ports list allows all ports.
