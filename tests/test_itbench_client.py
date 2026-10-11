import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.itbench.verdict import attach_verdict
from benchmarks.itbench.client import ITBenchEnvironmentClient, load_itbench_verdict
from benchmarks.itbench.summary import summarize
from utils.aworld_usage_capture import capture_aworld_usage
from utils.llm_usage import usage_from_result


class ITBenchEnvironmentClientTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.kubeconfig = Path(self.directory.name) / "restricted-kubeconfig"
        self.kubeconfig.write_text("test", encoding="utf-8")
        self.client = ITBenchEnvironmentClient(
            "itbench-sre-1-mitigation-1", "Resolve an active alert", self.kubeconfig,
        )

    def tearDown(self):
        self.directory.cleanup()

    @patch("benchmarks.itbench.client.subprocess.run")
    def test_probe_uses_restricted_kubeconfig(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "pod-a Running\n"
        run.return_value.stderr = ""
        result = self.client.execute_probe_action("exec_shell('kubectl get pods -n app')")
        self.assertIn("pod-a Running", result)
        self.assertEqual(
            run.call_args.args[0],
            ["kubectl", "--kubeconfig", str(self.kubeconfig.resolve()), "get", "pods", "-n", "app"],
        )

    @patch("benchmarks.itbench.client.subprocess.run")
    def test_probe_rejects_changes_and_context_override(self, run):
        self.assertIn("Probe cannot", self.client.execute_probe_action(
            "exec_shell('kubectl delete pod pod-a')"))
        self.assertIn("not allowed", self.client.execute_probe_action(
            "exec_shell('kubectl --context other get pods')"))
        run.assert_not_called()

    def test_submission_is_not_a_success_verdict(self):
        result = self.client.execute_action("submit()")
        self.assertTrue(result["is_submission"])
        self.assertTrue(self.client.submitted)
        self.assertFalse(self.client.is_problem_solved())

    @patch("benchmarks.itbench.client.urlopen")
    def test_prometheus_alerts_use_configured_endpoint(self, urlopen):
        client = ITBenchEnvironmentClient(
            "itbench-sre-1-mitigation-1", "Resolve alert", self.kubeconfig,
            "http://prometheus.example:9090",
        )
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"status":"success"}'
        result = client.execute_probe_action("get_alerts()")
        self.assertIn('"success"', result)
        self.assertEqual(urlopen.call_args.args[0], "http://prometheus.example:9090/api/v1/alerts")

    def test_only_external_boolean_verdict_counts(self):
        path = Path(self.directory.name) / "verdict.json"
        path.write_text(json.dumps({"passed": True}), encoding="utf-8")
        self.assertTrue(load_itbench_verdict(path)["passed"])
        path.write_text(json.dumps({"status": "submitted"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_itbench_verdict(path)

    def test_attach_verdict_updates_saved_record(self):
        record_path = Path(self.directory.name) / "attempt.json"
        verdict_path = Path(self.directory.name) / "verdict.json"
        record_path.write_text(json.dumps({
            "benchmark": "ITBench", "task_id": "itbench-sre-1-mitigation-1",
            "success": None,
        }), encoding="utf-8")
        verdict_path.write_text('{"passed": false}', encoding="utf-8")
        record = attach_verdict(record_path, verdict_path)
        self.assertIs(record["success"], False)
        self.assertIs(json.loads(record_path.read_text())["success"], False)

    def test_token_usage_handles_aworld_result_shape(self):
        result = type("Result", (), {"usage": {
            "prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16,
        }})()
        self.assertEqual(usage_from_result(result), {
            "input_tokens": 12, "output_tokens": 4, "total_tokens": 16,
        })

    def test_provider_usage_capture(self):
        import aworld.models.openai_provider as provider_module
        with patch.object(provider_module, "usage_process") as original:
            with capture_aworld_usage() as calls:
                provider_module.usage_process({
                    "prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16,
                })
            self.assertEqual(calls[0]["total_tokens"], 16)
            original.assert_called_once()

    def test_summary_excludes_unverified_attempts_from_success_rate(self):
        result = summarize([
            {"success": True, "duration_seconds": 10, "tool_calls": [{}], "cost_usd": 0.2},
            {"success": False, "duration_seconds": 20, "tool_calls": [{}, {}], "cost_usd": 0.4},
            {"success": None, "duration_seconds": 30, "tool_calls": []},
        ])
        self.assertEqual(result["success_rate"], 0.5)
        self.assertEqual(result["unverified_attempts"], 1)
        self.assertEqual(result["mean_duration_seconds"], 15)


if __name__ == "__main__":
    unittest.main()
