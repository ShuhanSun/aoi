"""Local ITBench SRE environment adapter for the existing AOI harness.

ITBench owns scenario deployment and grading. This client connects AOI to an
already started scenario using ITBench's restricted kubeconfig. Submission is
recorded, never interpreted as a passing benchmark verdict.
"""

import ast
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen


READ_ONLY_VERBS = {
    "get", "describe", "logs", "top", "api-resources", "api-versions",
    "cluster-info", "version", "explain",
}
GLOBAL_OPTIONS_WITH_VALUES = {"-n", "--namespace", "--request-timeout"}
FORBIDDEN_CONNECTION_OPTIONS = {
    "--kubeconfig", "--context", "--server", "--user", "--cluster",
    "--token", "--client-certificate", "--client-key", "--certificate-authority",
}


def _kubectl_verb(args: list[str]) -> str:
    index = 0
    while index < len(args):
        token = args[index]
        if token in GLOBAL_OPTIONS_WITH_VALUES:
            index += 2
        elif token.startswith("-"):
            index += 1
        else:
            return token
    return ""


class ITBenchEnvironmentClient:
    benchmark_name = "ITBench"

    def __init__(self, scenario_id: str, incident: str, kubeconfig: Path,
                 prometheus_url: str | None = None):
        self.scenario_id = scenario_id
        self.incident = incident
        self.kubeconfig = Path(kubeconfig).expanduser().resolve()
        if not self.kubeconfig.is_file():
            raise FileNotFoundError(f"ITBench kubeconfig not found: {self.kubeconfig}")
        if prometheus_url:
            parsed = urlparse(prometheus_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
                raise ValueError("prometheus_url must be an HTTP(S) origin without credentials")
            self.prometheus_url = prometheus_url.rstrip("/")
        else:
            self.prometheus_url = None
        self.session_id = f"itbench-{scenario_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"
        self.submitted = False
        self.tool_calls: list[dict[str, Any]] = []

    def init_problem(self, problem_id: str) -> dict[str, Any]:
        if problem_id != self.scenario_id:
            raise ValueError(f"Expected ITBench scenario {self.scenario_id}, got {problem_id}")
        return {
            "session_id": self.session_id,
            "problem_id": problem_id,
            "task_description": self.incident,
            "instructions": (
                "Use exec_shell('kubectl ...') for Kubernetes investigation and repair. "
                "If Prometheus is configured, use get_alerts() or query_prometheus('PromQL'). "
                "Probe may only inspect; Executor may change resources. "
                "Use submit() when finished. The external ITBench grader determines success."
            ),
            "available_actions": {
                "exec_shell": "Run kubectl against ITBench's restricted kubeconfig",
                "get_alerts": "Read active Prometheus alerts when configured",
                "query_prometheus": "Run one read-only PromQL query when configured",
                "submit": "Finish this attempt; grading happens separately",
            },
        }

    def get_session_id(self) -> str:
        return self.session_id

    def get_submit_format(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "problem_id": self.scenario_id,
            "submit_api": {"api_name": "submit", "description": "Call submit() after repair and verification"},
            "is_already_submitted": self.submitted,
        }

    def execute_probe_action(self, action: str) -> str:
        result = self._execute(action, role="probe")
        return f"Error: {result['result']}" if result.get("error") else result["result"]

    def execute_action(self, action: str) -> dict[str, Any]:
        return self._execute(action, role="executor")

    def _execute(self, action: str, role: str) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        try:
            expression = ast.parse(action.strip(), mode="eval").body
            if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
                raise ValueError("Expected exec_shell('kubectl ...') or submit()")
            if expression.func.id == "submit":
                if expression.args or expression.keywords or role == "probe":
                    raise ValueError("submit() takes no arguments and is unavailable to Probe")
                self.submitted = True
                result = {"result": "complete: submitted for ITBench evaluation", "is_submission": True}
            elif expression.func.id in {"get_alerts", "query_prometheus"}:
                if not self.prometheus_url:
                    raise ValueError("Prometheus URL was not configured")
                if expression.keywords:
                    raise ValueError("Keyword arguments are unsupported")
                if expression.func.id == "get_alerts":
                    if expression.args:
                        raise ValueError("get_alerts() takes no arguments")
                    endpoint = "/api/v1/alerts"
                else:
                    if len(expression.args) != 1:
                        raise ValueError("query_prometheus requires one PromQL string")
                    query = ast.literal_eval(expression.args[0])
                    if not isinstance(query, str):
                        raise ValueError("PromQL must be a string")
                    endpoint = "/api/v1/query?" + urlencode({"query": query})
                with urlopen(self.prometheus_url + endpoint, timeout=20) as response:
                    result = {"result": response.read(200_000).decode("utf-8", errors="replace")}
            elif expression.func.id == "exec_shell":
                if len(expression.args) != 1 or expression.keywords:
                    raise ValueError("exec_shell requires one string command")
                command = ast.literal_eval(expression.args[0])
                if not isinstance(command, str):
                    raise ValueError("exec_shell command must be a string")
                argv = shlex.split(command)
                if not argv or argv[0] != "kubectl":
                    raise ValueError("Only kubectl commands are supported by this ITBench adapter")
                if any(token in argv for token in (";", "|", "&&", "||", ">", "<")):
                    raise ValueError("Shell operators are unsupported; issue one command per call")
                if any(arg.split("=", 1)[0] in FORBIDDEN_CONNECTION_OPTIONS for arg in argv[1:]):
                    raise ValueError("Changing kubeconfig or connection settings is not allowed")
                verb = _kubectl_verb(argv[1:])
                if role == "probe" and verb not in READ_ONLY_VERBS:
                    raise ValueError(f"Probe cannot run state-changing kubectl verb: {verb}")
                completed = subprocess.run(
                    ["kubectl", "--kubeconfig", str(self.kubeconfig), *argv[1:]],
                    capture_output=True, text=True, timeout=90, check=False,
                )
                output = (completed.stdout + completed.stderr).strip()
                result = {"result": output or f"kubectl exited {completed.returncode}"}
                if completed.returncode:
                    result["error"] = True
            else:
                raise ValueError(f"Unsupported action: {expression.func.id}")
        except (SyntaxError, ValueError, TypeError, subprocess.TimeoutExpired, OSError) as exc:
            result = {"result": str(exc), "error": True}
        self.tool_calls.append({
            "timestamp": started.isoformat(), "role": role, "action": action,
            "duration_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
            "result": result,
        })
        return result

    def is_problem_solved(self) -> bool:
        # Only ITBench's verifier can establish benchmark success.
        return False

    def cleanup_session(self) -> bool:
        # Scenario lifecycle belongs to ITBench, not this adapter.
        return True


def load_itbench_verdict(path: Path | None) -> dict[str, Any] | None:
    """Read an externally produced verdict without inferring success from AOI output."""
    if path is None:
        return None
    verdict = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(verdict, dict):
        raise ValueError("ITBench verdict must be a JSON object")
    success = verdict.get("success", verdict.get("passed"))
    if not isinstance(success, bool):
        raise ValueError("ITBench verdict needs a boolean success or passed field")
    return verdict
