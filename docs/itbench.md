# ITBench SRE mitigation

AOI can run against a locally deployed ITBench SRE scenario. ITBench owns
deployment, fault injection, restricted agent access, and grading. This adapter
reuses AOI's Observer, Probe, Executor, and Compressor. It does not treat an
AOI `submit()` call as a passing ITBench verdict.

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

After grading, summarize all local attempts with:

```sh
.venv/bin/python -m benchmarks.itbench.summary res/itbench --output res/itbench/summary.json
```

The summary counts unverified attempts separately and computes success rate
only over externally graded attempts.

A redacted record and report from a three-attempt local run of archived SRE
scenario 1 are available in [reports/itbench/2026-10-10-sre-1](../reports/itbench/2026-10-10-sre-1/report.md).
