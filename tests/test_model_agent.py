import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from freeciv_agent.model_agent import generate, open_response
from urllib.request import Request


class Response(io.BytesIO):
    def __init__(self, body, content_type):
        super().__init__(body)
        self.headers = {'Content-Type': content_type}


class ModelAgentTests(unittest.TestCase):
    config = {'base_url': 'http://localhost/private-capability/v1', 'model': 'test'}

    def test_stream_returns_completed_plan(self):
        plan = {'actions': [{'name': 'freeciv_chat', 'arguments': {'message': 'Hello'}}]}
        message = {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(plan)}]}
        body = ('data: ' + json.dumps({'type': 'response.completed', 'response': {'output': [message]}}) + '\n\n').encode()
        with patch('freeciv_agent.model_agent.open_response', return_value=Response(body, 'text/event-stream')) as send:
            self.assertEqual(generate(self.config, {'event': {'kind': 'chat'}}), plan)
        request = send.call_args.args[0]
        sent = json.loads(request.data)
        self.assertFalse(sent['store'])
        self.assertNotIn('tools', sent)  # Model cannot execute computer tools.

    def test_partial_stream_never_yields_actions(self):
        event = {'type': 'response.output_text.delta', 'delta': '{"actions":[]}'}
        with patch('freeciv_agent.model_agent.open_response', return_value=Response(('data: ' + json.dumps(event) + '\n').encode(), 'text/event-stream')):
            with self.assertRaisesRegex(RuntimeError, 'before completion'):
                generate(self.config, {})

    def test_http_error_hides_endpoint_and_credential(self):
        error = HTTPError(self.config['base_url'], 401, 'secret', {}, None)
        with patch('freeciv_agent.model_agent.open_response', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, '^Model endpoint returned HTTP 401$'):
                generate(self.config, {})

    def test_non_streaming_compatible_response(self):
        body = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"actions":[]}'}]}]}
        with patch('freeciv_agent.model_agent.open_response', return_value=Response(json.dumps(body).encode(), 'application/json')):
            self.assertEqual(generate(self.config, {}), {'actions': []})

    def test_stream_without_content_type(self):
        event = {'type': 'response.completed', 'response': {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"actions":[]}'}]}]}}
        with patch('freeciv_agent.model_agent.open_response', return_value=Response(('data: ' + json.dumps(event) + '\n').encode(), '')):
            self.assertEqual(generate(self.config, {}), {'actions': []})

    def test_loopback_never_uses_system_proxy(self):
        with patch('freeciv_agent.model_agent.build_opener') as build, patch('freeciv_agent.model_agent.urlopen') as remote:
            open_response(Request('http://127.0.0.1:4202/private/v1/responses'), 5)
        self.assertEqual(build.call_args.args[0].proxies, {})
        remote.assert_not_called()
