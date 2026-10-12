import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from benchmarks.itbench.client import ITBenchEnvironmentClient


class CommandValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        kubeconfig = Path(self.temp.name) / 'config'; kubeconfig.write_text('test')
        self.client = ITBenchEnvironmentClient('test', 'Investigate', kubeconfig)

    @patch('benchmarks.itbench.client.subprocess.run')
    def test_non_string_commands_are_logged_errors_and_can_retry(self, run):
        for action in (['exec_kubectl', ['get', 'pods']], {'command': 'get pods'}, None, 42):
            result = self.client.execute_action(action)
            self.assertTrue(result['error'])
            self.assertIn('Python call string', result['result'])
            self.assertEqual(self.client.tool_calls[-1]['action'], action)
        run.assert_not_called()
        run.return_value.returncode = 0; run.return_value.stdout = 'ok'; run.return_value.stderr = ''
        result = self.client.execute_action("exec_kubectl(['get', 'pods'])")
        self.assertNotIn('error', result)
        self.assertEqual(len(self.client.tool_calls), 5)

    @patch('benchmarks.itbench.client.subprocess.run')
    def test_probe_non_string_has_actionable_error(self, run):
        result = self.client.execute_probe_action(['exec_kubectl', ['get', 'pods']])
        self.assertIn('Error: Action must', result)
        self.assertTrue(self.client.tool_calls[-1]['result']['error'])
        run.assert_not_called()

    @patch('benchmarks.itbench.client.subprocess.run')
    def test_raw_multiline_string_is_rejected_with_specific_guidance(self, run):
        result = self.client.execute_action("exec_kubectl(['apply', '-f', '-'], stdin='kind: ConfigMap\nmetadata:\n  name: test\n')")
        self.assertTrue(result['error'])
        self.assertIn('triple-quoted', result['result'])
        self.assertIn('\\n', result['result'])
        self.assertTrue(self.client.tool_calls[-1]['result']['error'])
        run.assert_not_called()

    @patch('benchmarks.itbench.client.subprocess.run')
    def test_escaped_and_triple_quoted_multiline_stdin_are_identical(self, run):
        run.return_value.returncode = 0; run.return_value.stdout = 'configured'; run.return_value.stderr = ''
        manifest = 'kind: ConfigMap\nmetadata:\n  name: test\n'
        for literal in (repr(manifest), "'''" + manifest + "'''"):
            result = self.client.execute_action(f"exec_kubectl(['apply', '-f', '-'], stdin={literal})")
            self.assertNotIn('error', result)
            self.assertEqual(run.call_args.kwargs['input'], manifest)
