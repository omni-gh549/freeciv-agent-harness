import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from freeciv_agent.client import GameClient
from freeciv_agent.events import EventLog
from freeciv_agent.http_api import make_server
from freeciv_agent.mcp import dispatch, run
from freeciv_agent.protocol import Protocol, bitset_has
from freeciv_agent.service import GameService, TOOL_NAMES
from freeciv_agent.state import GameState
from freeciv_agent.tools import TOOLS, validate


class FakeClient:
    def __init__(self):
        self.protocol = Protocol()
        self.events = EventLog()
        self.state = GameState(self.protocol, self.events)
        self.action_lock = threading.RLock()
        self.sent = []
        self.state.joined = True
        self.state.running = True
        self.state.conn_id = 7
        self.state.tables['connections'][7] = {'id': 7, 'player_num': 1}
        self.state.tables['players'] = {1: {'playerno': 1, 'name': 'Agent', 'gold': 99, 'phase_done': False},
                                       2: {'playerno': 2, 'name': 'Other', 'gold': 99999}}
        self.state.game = {'turn': 10}
        self.state.map = {'xsize': 8, 'ysize': 8, 'wrap_id': 1}
        self.state.tables['units'] = {4: {'id': 4, 'owner': 1, 'tile': 9, 'movesleft': 3},
                                      5: {'id': 5, 'owner': 2, 'tile': 10, 'movesleft': 3}}
        self.state.tables['cities'] = {2: {'id': 2, 'owner': 1, 'name': 'Home', 'can_build_unit': [2]},
                                       3: {'id': 3, 'owner': 2, 'name': 'Foreign'}}

    def request(self, name, fields, predicate=None, timeout=10):
        self.sent.append((name, fields))
        if name == 'player_phase_done':
            time.sleep(0.01)
            self.state.tables['players'][1]['phase_done'] = True
        return {'confirmed': True}

    def disconnect(self):
        self.state.joined = False
        return {'disconnected': True}


class ProtocolTests(unittest.TestCase):
    def test_profile_has_bidirectional_wire_facts(self):
        p = Protocol()
        self.assertEqual(p.name(259), 'web_player_info_addition')
        self.assertEqual(p.packet('chat_msg_req', message='hi'), {'pid': 26, 'message': 'hi'})
        self.assertGreater(len(p.packets), 150)

    def test_server_packets_cannot_be_sent(self):
        with self.assertRaises(ValueError):
            Protocol().packet('player_info', playerno=1)

    def test_bit_vectors(self):
        self.assertTrue(bitset_has([0, 2], 9))
        self.assertTrue(bitset_has({'raw': [1]}, 0))
        self.assertFalse(bitset_has([1], 8))
        self.assertFalse(bitset_has(None, 0))


class EventTests(unittest.TestCase):
    def test_pagination_cursor(self):
        e = EventLog()
        for i in range(5): e.emit('turn', turn=i)
        first = e.read(limit=2)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['cursor'], 2)
        self.assertEqual(e.read(after=2)['events'][0]['turn'], 2)

    def test_filtered_cursor_skips_nonmatching_events(self):
        e = EventLog()
        e.emit('chat', text='a'); e.emit('turn', turn=1); e.emit('chat', text='b')
        page = e.read(kinds=['turn'])
        self.assertEqual(page['cursor'], 3)
        self.assertEqual(len(page['events']), 1)

    def test_expired_cursor_reports_truncation(self):
        e = EventLog(capacity=2)
        for i in range(3): e.emit('turn', turn=i)
        self.assertTrue(e.read()['truncated'])
        self.assertFalse(e.read(after=1)['truncated'])

    def test_wait_wakes_on_event(self):
        e = EventLog()
        timer = threading.Timer(0.02, lambda: e.emit('turn', turn=7))
        timer.start()
        self.assertEqual(e.read(timeout=1)['events'][0]['turn'], 7)
        timer.join()

    def test_journal_replay_continues_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            EventLog(journal=path).emit('turn', turn=3)
            log = EventLog(journal=path)
            self.assertEqual(log.emit('chat', text='hi')['sequence'], 2)

    def test_copies_are_isolated(self):
        e = EventLog(); e.emit('chat', data={'a': 1})
        page = e.read(); page['events'][0]['data']['a'] = 2
        self.assertEqual(e.read()['events'][0]['data']['a'], 1)

    def test_bounds(self):
        for args in ({'after': -1}, {'timeout': 60}, {'limit': 0}):
            with self.assertRaises(ValueError): EventLog().read(**args)

    def test_cursor_from_an_old_process_resets_explicitly(self):
        result = EventLog().read(after=100)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['cursor'], 0)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.events = EventLog()
        self.s = GameState(Protocol(), self.events)

    def test_partial_updates_preserve_income(self):
        self.s.apply({'pid': 51, 'playerno': 1, 'name': 'Agent', 'gold': 20})
        self.s.apply({'pid': 259, 'playerno': 1, 'expected_income': 5})
        self.s.apply({'pid': 51, 'playerno': 1, 'gold': 25})
        self.assertEqual(self.s.tables['players'][1]['expected_income'], 5)

    def test_turn_event_only_at_begin_boundary_and_deduped(self):
        self.s.apply({'pid': 127, 'turn': 6, 'year': -100})
        self.assertFalse(self.events.read()['events'])
        self.s.apply({'pid': 128}); self.s.apply({'pid': 128})
        self.assertEqual(len(self.events.read()['events']), 1)

    def test_connection_assigns_player_not_username_guess(self):
        self.s.apply({'pid': 51, 'playerno': 3, 'name': 'OtherName'})
        self.s.apply({'pid': 5, 'you_can_join': True, 'conn_id': 9})
        self.s.apply({'pid': 115, 'id': 9, 'used': True, 'player_num': 3, 'observer': False})
        self.assertEqual(self.s.player_id, 3)

    def test_observer_cannot_control(self):
        self.s.conn_id = 9
        self.s.tables['players'][3] = {'playerno': 3}
        self.s.tables['connections'][9] = {'player_num': 3, 'observer': True}
        self.assertIsNone(self.s.player_id)

    def test_chat_has_sender_and_plain_text(self):
        self.s.tables['connections'][1] = {'username': 'Human'}
        self.s.apply({'pid': 25, 'conn_id': 1, 'message': '<b>Hello</b> &amp; welcome'})
        event = self.events.read()['events'][0]
        self.assertEqual((event['kind'], event['sender'], event['text']), ('chat', 'Human', 'Hello & welcome'))

    def test_disconnect_connection_removed(self):
        self.s.apply({'pid': 115, 'id': 1, 'used': True, 'player_num': 2})
        self.s.apply({'pid': 115, 'id': 1, 'used': False})
        self.assertNotIn(1, self.s.tables['connections'])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.s = GameService(self.client)

    def test_catalog_matches_implementation(self):
        self.assertEqual(TOOL_NAMES, {t['name'] for t in TOOLS})

    def test_only_own_units_and_cities(self):
        self.assertEqual([u['id'] for u in self.s.units()], [4])
        self.assertEqual([c['id'] for c in self.s.cities()], [2])

    def test_public_players_do_not_leak_gold(self):
        self.assertNotIn('gold', self.s.status()['players'][1])
        self.assertEqual(self.s.status()['me']['gold'], 99)

    def test_foreign_unit_control_rejected(self):
        with self.assertRaises(ValueError): self.s.move(5, 'N', 10)
        self.assertFalse(self.client.sent)

    def test_foreign_city_control_rejected(self):
        with self.assertRaises(ValueError): self.s.production(3, 'unit', 1, 10)
        self.assertFalse(self.client.sent)

    def test_stale_turn_rejected(self):
        with self.assertRaises(ValueError): self.s.end_turn(9)
        self.assertFalse(self.client.sent)

    def test_end_turn_idempotent(self):
        self.s.call('freeciv_end_turn', {'expected_turn': 10})
        self.assertTrue(self.s.call('freeciv_end_turn', {'expected_turn': 10})['already_done'])
        self.assertEqual(len(self.client.sent), 1)

    def test_concurrent_end_turn_does_not_double_send(self):
        threads = [threading.Thread(target=lambda: self.s.call('freeciv_end_turn', {'expected_turn': 10})) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(self.client.sent), 1)

    def test_chat_cannot_run_commands(self):
        for message in ('/load danger', ' /set timeout 5', 'a\nb', 'x' * 351):
            with self.assertRaises(ValueError): self.s.chat(message)

    def test_chat_matches_webclient_escaping(self):
        self.s.chat('  Who\'s "ready"? A&B <peace> 日本  ')
        self.assertEqual(self.client.sent[-1][1]['message'],
            'Who%26apos%3Bs%20%26quot%3Bready%26quot%3B%3F%20A%26amp%3BB%20%26lt%3Bpeace%26gt%3B%20%E6%97%A5%E6%9C%AC')

    def test_chat_rejects_encoded_packet_overflow(self):
        with self.assertRaisesRegex(ValueError, 'packet limit'):
            self.s.chat('日本語' * 100)
        self.assertFalse(self.client.sent)

    def test_load_rejects_live_game_and_paths(self):
        with self.assertRaises(ValueError): self.s.load('save')
        self.client.state.running = False
        for name in ('../save', '/tmp/save', 'a;quit', 'save.sav'):
            with self.assertRaises(ValueError): self.s.load(name)

    def test_map_omits_unknown(self):
        self.client.state.tables['tiles'] = {1: {'tile': 1, 'known': 0}, 2: {'tile': 2, 'known': 2}}
        self.assertEqual([t['tile'] for t in self.s.map()['tiles']], [2])

    def test_buildable_production_required(self):
        with self.assertRaises(ValueError): self.s.production(2, 'unit', 7)
        self.s.production(2, 'unit', 1)
        self.assertEqual(self.client.sent[0][1]['production_kind'], 61)

    def test_direction_and_bounds(self):
        with self.assertRaises(ValueError): self.s.move(4, 'invalid')
        self.s.move(4, 'NE')
        self.assertEqual(self.client.sent[0][1]['dest_tile'], 2)

    def test_rates_must_sum_to_100(self):
        with self.assertRaises(ValueError): self.s.rates(50, 60, 0)

    def test_only_safe_settings_and_no_command_injection(self):
        with self.assertRaises(ValueError): self.s.setting('fogofwar', 'false')
        with self.assertRaises(ValueError): self.s.setting('timeout', '0\nquit')
        with self.assertRaises(ValueError): self.s.setting('size', '4')
        self.s.setting('timeout', '0')
        self.assertEqual(self.client.sent[0][1]['message'], '/set timeout 0')

    def test_nations_match_their_adjective(self):
        self.client.state.tables['nations'][9] = {'id': 9, 'adjective': 'Japanese'}
        self.assertEqual(self.s.ruleset('nations', 'japan')[0]['id'], 9)

    def test_unknown_tools_and_parameters(self):
        for name, args in [('freeciv_status', {'unexpected': 1}), ('freeciv_end_turn', {'expected_turn': True}), ('freeciv_unknown', {})]:
            with self.assertRaises(ValueError): self.s.call(name, args)


class ClientTests(unittest.TestCase):
    def test_rejects_unsafe_connection_urls_before_open(self):
        c = GameClient()
        for url in ('file:///tmp/game', 'http://user:secret@localhost', 'http://localhost/path', 'ws://localhost'):
            with self.assertRaises(ValueError): c.connect(url)

    def test_unconfirmed_send_is_not_success(self):
        c = GameClient()
        class Socket:
            def send(self, value): pass
        c.ws = Socket(); c.state.joined = True
        result = c.request('chat_msg_req', {'message': 'test'}, timeout=0.01)
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['confirmation'], 'unconfirmed')

    def test_predicate_alone_does_not_skip_processing_boundary(self):
        c = GameClient()
        class Socket:
            def send(self, value): pass
        c.ws = Socket(); c.state.joined = True
        self.assertFalse(c.request('chat_msg_req', {'message': 'test'}, predicate=lambda: True, timeout=0.01)['confirmed'])


class MCPTests(unittest.TestCase):
    def test_initialize_negotiates_protocol(self):
        result = dispatch(GameService(FakeClient()), {'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}})
        self.assertEqual(result['protocolVersion'], '2024-11-05')
        self.assertIn('tools', result['capabilities'])

    def test_bad_tool_arguments_are_tool_errors(self):
        result = dispatch(GameService(FakeClient()), {'method': 'tools/call', 'params': {'name': 'freeciv_end_turn', 'arguments': {}}})
        self.assertTrue(result['isError'])

    def test_stdio_jsonrpc_roundtrip_and_parse_error(self):
        requests = '\n'.join(['not json', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}}), json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})]) + '\n'
        out = io.StringIO()
        run(GameService(FakeClient()), io.StringIO(requests), out)
        responses = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(responses[0]['error']['code'], -32700)
        self.assertTrue(any(r.get('id') == 2 and len(r['result']['tools']) >= 18 for r in responses))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server, self.runtime = make_server(GameService(FakeClient()), Path(self.temp.name), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, token=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if token: headers['Authorization'] = 'Bearer ' + self.runtime['token']
        if origin: headers['Origin'] = origin
        return Request(self.runtime['url'] + '/call', data=b'{"name":"freeciv_status"}', headers=headers)

    def test_authorized_native_call(self):
        with urlopen(self.request()) as r:
            self.assertEqual(json.load(r)['result']['me']['gold'], 99)

    def test_unauthorized_rejected(self):
        with self.assertRaises(HTTPError) as error: urlopen(self.request(token=False))
        self.assertEqual(error.exception.code, 401)
        error.exception.close()

    def test_browser_origin_rejected_even_with_token(self):
        with self.assertRaises(HTTPError) as error: urlopen(self.request(origin='https://example.com'))
        self.assertEqual(error.exception.code, 403)
        error.exception.close()


if __name__ == '__main__':
    unittest.main()
