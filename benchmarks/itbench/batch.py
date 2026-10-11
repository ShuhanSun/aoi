"""Resume a serial ITBench SRE suite on an explicitly isolated Kind cluster."""

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import signal
import shutil
import subprocess
import sys
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from benchmarks.itbench.summary import summarize


APP_NAMESPACES = {"opentelemetry-demo": "otel-demo", "book-info": "book-info"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def catalog(itbench_dir):
    cases = []
    for path in sorted((itbench_dir / "library/scenarios").glob("*/scenario.yaml"),
                       key=lambda item: int(item.parent.name)):
        scenario = yaml.safe_load(path.read_text())
        if "sre" not in scenario.get("environment", {}).get("domains", []):
            continue
        number = int(path.parent.name)
        archive = itbench_dir / f"archive/scenarios/{number}/scenario.yaml"
        if not archive.is_file():
            raise ValueError(f"Scenario {number} has no executable archive counterpart")
        archived = yaml.safe_load(archive.read_text())
        library_faults = [item["id"] for group in scenario["faults"] for item in group["injections"]]
        archive_faults = [item["id"] for group in archived["faults"] for item in group["injections"]]
        if library_faults != archive_faults:
            raise ValueError(f"Scenario {number}: library and executable fault IDs differ")
        for current, executable in zip(
                [item for group in scenario["faults"] for item in group["injections"]],
                [item for group in archived["faults"] for item in group["injections"]]):
            targets = [target["kubernetes"] for target in current.get("targets", []) if "kubernetes" in target]
            old = executable.get("args", {}).get("kubernetesObject", {})
            metadata = old.get("metadata", {})
            if targets and not any((target.get("kind"), target.get("name"), target.get("namespace"))
                                   == (old.get("kind"), metadata.get("name"), metadata.get("namespace"))
                                   for target in targets):
                raise ValueError(f"Scenario {number}: library and executable targets differ")
        apps = [app["id"] for app in scenario["environment"]["applications"]]
        if any(app not in APP_NAMESPACES for app in apps):
            raise ValueError(f"Scenario {number} uses an unsupported application")
        extra_path = path.parent / "extra_variables.yaml"
        variables = yaml.safe_load(extra_path.read_text()) if extra_path.is_file() else {}
        variables = variables or {}
        variables["scenario_id"] = number
        variables["applications_opentelemetry_demo_enabled"] = "opentelemetry-demo" in apps
        variables["applications_book_info_enabled"] = "book-info" in apps
        namespaces = sorted({APP_NAMESPACES[app] for app in apps})
        # Keep evaluator/deployer metadata separate from the agent-visible incident.
        cases.append({"scenario_number": number, "catalog_source": str(path),
                      "catalog_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "execution_source": str(archive),
                      "execution_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                      "fault_ids": library_faults, "applications": apps,
                      "namespaces": namespaces, "deployment_variables": variables,
                      "incident": "An active SRE incident affects the application in namespace(s) "
                                  + ", ".join(namespaces)
                                  + ". Investigate current alerts, workload state and configuration; "
                                    "mitigate the incident and verify configuration and workload recovery before submitting."})
    return cases


class StageError(RuntimeError):
    pass


class Batch:
    def __init__(self, args):
        self.args = args
        self.root = Path(__file__).resolve().parents[2]
        self.itbench = args.itbench_dir.resolve()
        self.output = args.output.resolve()
        self.admin = args.admin_kubeconfig.resolve()
        self.env = {**os.environ, "KUBECONFIG": str(self.admin),
                    "UV_PYTHON": args.itbench_python,
                    "DOCKER_CONFIG": args.docker_config,
                    "SSL_CERT_FILE": os.environ.get("REQUESTS_CA_BUNDLE", os.environ.get("SSL_CERT_FILE", ""))}
        self.cases = catalog(self.itbench)
        self.progress = {"state": "running", "started_at": timestamp(), "pid": os.getpid(),
                         "total_scenarios": len(self.cases), "current_scenario": None,
                         "phase": "preflight", "phase_started_at": timestamp(), "completed": []}
        self.forward = None

    def phase(self, name, number=None):
        self.progress.update(phase=name, current_scenario=number, phase_started_at=timestamp())
        write_json(self.output / "progress.json", self.progress)
        print(f"{timestamp()} scenario={number} phase={name}", flush=True)

    def command(self, argv, logfile, timeout=600):
        logfile.parent.mkdir(parents=True, exist_ok=True)
        with logfile.open("a") as stream:
            process = subprocess.Popen(argv, cwd=self.root, env=self.env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise StageError(f"Stage timed out after {timeout}s; log: {logfile}")
        if code:
            raise StageError(f"Stage exited {code}; log: {logfile}")

    def make(self, target, number, logfile, timeout=600):
        inventory = yaml.safe_load((self.itbench / "scenarios/sre/inventory/group_vars/environment/cluster.yaml").read_text())
        if Path(inventory["cluster"]["kubeconfig"]).expanduser().resolve() != self.admin:
            raise StageError("ITBench inventory no longer targets the explicitly isolated admin kubeconfig")
        self.command(["make", "-C", str(self.itbench / "scenarios/sre"),
                      f"SCENARIO_NUMBER={number}", target, f"UV={self.args.uv_command}"],
                     logfile, timeout)

    def kubectl(self, *args):
        return ["kubectl", "--kubeconfig", str(self.admin), *args]

    def check_cluster(self):
        config = yaml.safe_load(self.admin.read_text())
        if config.get("current-context") != "kind-kind-dev":
            raise ValueError("Batch mutations are restricted to kind-kind-dev")
        nodes = json.loads(subprocess.check_output(self.kubectl("get", "nodes", "-o", "json"), timeout=30))
        if not nodes["items"] or any(not node["metadata"]["name"].startswith("kind-dev-") for node in nodes["items"]):
            raise ValueError("Unexpected cluster nodes; refusing suite cleanup")

    def cleanup(self, number, directory):
        self.phase("cleanup", number)
        self.make("remove-fault-resources", number, directory / "cleanup.log")
        self.command(["helm", "--kubeconfig", str(self.admin), "uninstall", "otel-demo",
                      "-n", "otel-demo", "--ignore-not-found", "--wait", "--timeout", "5m"],
                     directory / "cleanup.log")
        self.command(self.kubectl("delete", "namespace", "otel-demo", "book-info",
                                  "--ignore-not-found", "--wait=true", "--timeout=180s"),
                     directory / "cleanup.log", 240)

    def start_forward(self):
        if self.forward is not None:
            self.forward.terminate()
            self.forward.wait(timeout=10)
        stream = (self.output / "prometheus-forward.log").open("a")
        self.forward = subprocess.Popen(self.kubectl("port-forward", "-n", "prometheus",
                                                    "svc/prometheus-kube-prometheus-prometheus",
                                                    f"{self.args.prometheus_port}:9090"),
                                        stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        stream.close()
        time.sleep(2)
        if self.forward.poll() is not None:
            raise StageError("Prometheus forwarding failed; inspect prometheus-forward.log")

    def snapshot(self, case, directory, name):
        state = {}
        for namespace in case["namespaces"]:
            try:
                state[namespace] = json.loads(subprocess.check_output(
                    self.kubectl("get", "pods,deployments,services,networkpolicies,resourcequotas",
                                 "-n", namespace, "-o", "json"), timeout=30))
            except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
                state[namespace] = {"error": str(exc)}
        write_json(directory / name, state)

    def report(self):
        ledgers = [json.loads(path.read_text()) for path in sorted(self.output.glob("scenarios/*/case.json"))]
        records = [json.loads(path.read_text()) for path in sorted(self.output.glob("scenarios/*/task.json"))]
        summary = {**summarize(records), "requested_scenarios": len(self.cases),
                   "finished_scenarios": len(ledgers),
                   "case_status_counts": dict(Counter(item["status"] for item in ledgers)),
                   "suite_state": self.progress["state"]}
        write_json(self.output / "summary.json", summary)
        rows = []
        for item in ledgers:
            path = self.output / f"scenarios/{item['scenario_number']}/task.json"
            record = json.loads(path.read_text()) if path.is_file() else {}
            tokens = record.get("token_usage", {}).get("total", {}).get("total_tokens")
            rows.append(f"| {item['scenario_number']} | {item['status']} | {record.get('submitted')} | "
                        f"{record.get('success')} | {tokens} | {record.get('duration_seconds')} | {record.get('cost_usd')} |")
        report = ("# ITBench library SRE full-suite results\n\n"
                  f"Model: {self.args.model}; requested scenarios: {len(self.cases)}; "
                  f"finished: {len(ledgers)}; state: {self.progress['state']}.\n\n"
                  "Scenarios are selected from the pinned library catalog. ITBench's current "
                  "Make/Ansible runtime executes their archive counterparts; both source hashes "
                  "are stored in manifest.json. Ground truth and fault definitions are not passed "
                  "to the agent. Each scenario uses fresh application namespaces.\n\n"
                  "No official mitigation verifier is available in this local run. A submission "
                  "is not a success verdict; official success remains null. Deployment/injection "
                  "failures are retained separately rather than counted as model failures. "
                  "Post-run snapshots are evidence, not an official grade.\n\n"
                  "| Scenario | Execution status | Submitted | Official success | Tokens | Model seconds | Estimated USD |\n"
                  "|---|---|---|---|---:|---:|---:|\n" + "\n".join(rows) + "\n")
        (self.output / "report.md").write_text(report)
        if self.progress["state"] in {"completed", "blocked"}:
            with zipfile.ZipFile(self.output / "results.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                for path in self.output.rglob("*"):
                    if path.is_file() and path.name not in {"results.zip", "restricted-kubeconfig", "access.log", "batch.lock"}:
                        archive.write(path, str(path.relative_to(self.output)))

    def execute(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.check_cluster()
        manifest_path = self.output / "manifest.json"
        configuration = {"model": self.args.model, "temperature": 0, "max_iterations": 14,
                         "max_context_tokens": 4096, "max_output_tokens": 5000,
                         "input_usd_per_million": 0.15, "output_usd_per_million": 0.60,
                         "settle_seconds": self.args.settle_seconds, "model_timeout_seconds": self.args.model_timeout,
                         "catalog": "library", "admin_context": "kind-kind-dev"}
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text())
            if previous["configuration"] != configuration or previous["cases"] != self.cases:
                raise ValueError("Resume configuration or catalog differs from the saved suite")
        else:
            write_json(manifest_path, {"configuration": configuration, "cases": self.cases,
                       "aoi_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip(),
                       "itbench_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.itbench, text=True).strip(),
                       "itbench_patch_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD"], cwd=self.itbench)).hexdigest()})
        write_json(self.output / "pid.json", {"pid": os.getpid(), "started_at": timestamp()})
        self.phase("preflight")
        self.make("deploy-tools", self.cases[0]["scenario_number"], self.output / "preflight.log", 1800)
        self.start_forward()
        for case in self.cases:
            number = case["scenario_number"]
            directory = self.output / f"scenarios/{number}"
            if (directory / "case.json").is_file():
                old = json.loads((directory / "case.json").read_text())
                if old["status"] != "environment_error" or not self.args.retry_environment_errors:
                    self.progress["completed"].append(number)
                    continue
                history = directory / "history"
                history.mkdir(exist_ok=True)
                (directory / "case.json").replace(history / f"environment-error-{time.time_ns()}.json")
            began = time.monotonic()
            ledger = {"scenario_number": number, "started_at": timestamp(),
                      "configuration": configuration, "status": "environment_error", "error": None}
            try:
                self.cleanup(number, directory)
                self.phase("deployment", number)
                self.command([*shlex.split(self.args.uv_command), "run", "--directory", str(self.itbench / "scenarios/sre"),
                              "ansible-playbook", "-i", "inventory", "project/manage_applications.yaml",
                              "--tags", "install_applications", "--extra-vars", json.dumps(case["deployment_variables"])],
                             directory / "deployment.log", 1800)
                self.snapshot(case, directory, "baseline.json")
                self.phase("injection", number)
                self.make("inject-scenario-faults", number, directory / "injection.log", 600)
                self.phase("agent_access", number)
                self.make("enable-agent-access", number, directory / "access.log", 180)
                access_source = self.root / "res/itbench/runtime/kubeconfig"
                access = directory / "restricted-kubeconfig"
                access.write_bytes(access_source.read_bytes())
                access.chmod(0o600)
                access_source.chmod(0o600)
                self.phase("fault_settle", number)
                time.sleep(self.args.settle_seconds)
                self.snapshot(case, directory, "injected.json")
                self.phase("model", number)
                ledger["status"] = "model_error"
                self.command([sys.executable, "-u", "-m", "benchmarks.itbench.run",
                              "--itbench-dir", str(self.itbench), "--scenario-number", str(number),
                              "--incident", case["incident"], "--kubeconfig", str(access),
                              "--prometheus-url", f"http://127.0.0.1:{self.args.prometheus_port}",
                              "--model", self.args.model, "--temperature", "0", "--max-iterations", "14",
                              "--input-usd-per-million", "0.15", "--output-usd-per-million", "0.60",
                              "--output", str(directory / "task.json")],
                             directory / "model.log", self.args.model_timeout)
                record = json.loads((directory / "task.json").read_text())
                if record.get("raw_aoi_trace"):
                    trace = self.root / record["raw_aoi_trace"]
                    if trace.is_file():
                        shutil.copyfile(trace, directory / "raw-aoi-trace.json")
                ledger["status"] = "model_error" if record.get("error") else "executed_ungraded"
                ledger["error"] = record.get("error")
                self.snapshot(case, directory, "post-model.json")
            except (OSError, ValueError, subprocess.SubprocessError, StageError) as exc:
                ledger["error"] = str(exc)
            finally:
                ledger.update(ended_at=timestamp(), total_case_seconds=round(time.monotonic() - began, 3))
                write_json(directory / "case.json", ledger)
                self.cleanup(number, directory)
                access = directory / "restricted-kubeconfig"
                if access.exists():
                    access.unlink()
                self.progress["completed"].append(number)
                self.phase("between_scenarios")
                self.report()
        self.progress.update(state="completed", ended_at=timestamp())
        self.phase("completed")
        self.report()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--itbench-dir", type=Path, required=True)
    parser.add_argument("--admin-kubeconfig", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-4o-mini", choices=["gpt-4o-mini"])
    parser.add_argument("--uv-command", default="uvx --system-certs --from uv==0.12.24 uv")
    parser.add_argument("--itbench-python", default="/Users/shuhsun/.local/bin/python3.13")
    parser.add_argument("--docker-config", default="/private/tmp/aoi-public-oci")
    parser.add_argument("--prometheus-port", type=int, default=19090)
    parser.add_argument("--settle-seconds", type=int, default=30)
    parser.add_argument("--model-timeout", type=int, default=1800)
    parser.add_argument("--retry-environment-errors", action="store_true", help="Retry setup/injection errors on resume; preserve their earlier case ledgers")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        print(json.dumps(catalog(args.itbench_dir.resolve()), indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "batch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        batch = Batch(args)
        try:
            batch.execute()
        except (Exception, KeyboardInterrupt) as exc:
            batch.progress.update(state="blocked", error=f"{type(exc).__name__}: {exc}")
            write_json(args.output / "progress.json", batch.progress)
            batch.report()
            raise
        finally:
            if batch.forward is not None and batch.forward.poll() is None:
                batch.forward.terminate()
                batch.forward.wait(timeout=10)


if __name__ == "__main__":
    main()
