import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from freeciv_agent.runner import decide
from freeciv_agent.service import GameService
from test_core import FakeClient


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.service = GameService(FakeClient())

    def output(self, actions):
        return subprocess.CompletedProcess(['agent'], 0, json.dumps({'actions': actions}), '')

    def test_injects_observed_turn_and_executes_plan(self):
        with patch('freeciv_agent.runner.subprocess.run', return_value=self.output([{'name': 'freeciv_end_turn'}])) as proc:
            result = decide(self.service, ['agent'], {'kind': 'turn', 'turn': 10})
        self.assertEqual(self.service.client.sent, [('player_phase_done', {'turn': 10})])
        observation = json.loads(proc.call_args.kwargs['input'])
        self.assertNotIn('gold', observation['status']['players'][1])
        self.assertEqual(len(result['actions']), 1)

    def test_validates_entire_plan_before_first_action(self):
        plan = [{'name': 'freeciv_end_turn', 'arguments': {'expected_turn': 10}}, {'name': 'freeciv_load', 'arguments': {'filename': 'other'}}]
        with patch('freeciv_agent.runner.subprocess.run', return_value=self.output(plan)):
            with self.assertRaises(ValueError): decide(self.service, ['agent'], {'kind': 'turn'})
        self.assertFalse(self.service.client.sent)

    def test_turn_change_while_agent_thinks_stops_plan(self):
        def agent(*args, **kwargs):
            self.service.state.game['turn'] = 11
            return self.output([{'name': 'freeciv_end_turn', 'arguments': {'expected_turn': 10}}])
        with patch('freeciv_agent.runner.subprocess.run', side_effect=agent):
            result = decide(self.service, ['agent'], {'kind': 'turn'})
        self.assertFalse(self.service.client.sent)
        self.assertIn('stopped', result['actions'][0])

    def test_agent_failure_is_explicit(self):
        with patch('freeciv_agent.runner.subprocess.run', return_value=subprocess.CompletedProcess(['agent'], 2, '', 'failed')):
            with self.assertRaises(RuntimeError): decide(self.service, ['agent'], {'kind': 'turn'})

    def test_real_agent_subprocess_protocol(self):
        command = [sys.executable, '-c', 'import json,sys; o=json.load(sys.stdin); print(json.dumps({"actions":[{"name":"freeciv_status","arguments":{}}]}))']
        result = decide(self.service, command, {'kind': 'turn', 'turn': 10})
        self.assertEqual(result['actions'][0]['result']['game']['turn'], 10)

    def test_no_shell_command_string(self):
        with self.assertRaises(ValueError): decide(self.service, 'agent --run', {'kind': 'turn'})


if __name__ == '__main__':
    unittest.main()
