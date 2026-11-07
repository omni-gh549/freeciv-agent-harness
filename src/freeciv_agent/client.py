import copy
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from websockets.sync.client import connect
from websockets.exceptions import ConnectionClosed

from .events import EventLog
from .protocol import Protocol
from .state import GameState


class GameClient:
    def __init__(self, protocol=None, events=None):
        self.protocol = protocol or Protocol()
        self.events = events or EventLog()
        self.state = GameState(self.protocol, self.events)
        self.ws = None
        self.thread = None
        self.stop = threading.Event()
        self.action_lock = threading.RLock()
        self.connection_lock = threading.Lock()
        self.error = None

    def connect(self, url='http://localhost:8080', name='Agent', game_port=6001, password=None, timeout=20):
        if not isinstance(name, str) or not name.strip() or len(name) > 32 or any(ord(c) < 32 for c in name):
            raise ValueError('Player name must contain 1..32 printable characters')
        if not isinstance(game_port, int) or not 1 <= game_port <= 64535:
            raise ValueError('Game port must be 1..64535')
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError('Use an http(s) Freeciv-web origin without embedded credentials')
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError('Pass the server origin only, e.g. http://localhost:8080')
        with self.connection_lock:
            self.disconnect()
            self.state.reset()
            self.stop.clear()
            self.error = None
            origin = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
            endpoint = urlunsplit(('wss' if parsed.scheme == 'https' else 'ws', parsed.netloc,
                                   '/civsocket/' + str(game_port + 1000), '', ''))
            self.ws = connect(endpoint, origin=origin, proxy=None, open_timeout=timeout,
                              close_timeout=3, max_size=32 * 1024 * 1024)
            login = self.protocol.packet('server_join_req', username=name,
                capability=self.protocol.profile['capability'], version_label='-dev',
                major_version=3, minor_version=1, patch_version=90,
                port=str(game_port), password=hashlib.sha512(password.encode()).hexdigest() if password else None)
            self.ws.send(json.dumps(login))
            self.thread = threading.Thread(target=self._receive, daemon=True)
            self.thread.start()
            try:
                self.wait(lambda: self.state.rejected is not None or (self.state.joined and self.state.conn_id in self.state.tables['connections']), timeout)
                if self.state.rejected:
                    raise RuntimeError(self.state.rejected)
                # A ready connection includes initial player/ruleset packets; wait for a
                # short quiet boundary without requiring a human player assignment.
                deadline = time.monotonic() + min(timeout, 5)
                with self.state.condition:
                    while time.monotonic() < deadline:
                        before = self.state.revision
                        self.state.condition.wait(0.15)
                        if before == self.state.revision:
                            break
                # Initial state can be emitted before the server processes our
                # GUI_WEB declaration. Refresh owned cities missing web additions.
                with self.state.condition:
                    missing = [c['id'] for c in self.state.tables['cities'].values()
                               if c.get('owner') == self.state.player_id and 'can_build_unit' not in c]
                for city_id in missing:
                    self.request('city_refresh', {'city_id': city_id},
                        lambda cid=city_id: 'can_build_unit' in self.state.tables['cities'].get(cid, {}), timeout=3)
                self.events.emit('connected', name=name, url=origin, game_port=game_port)
                return self.state.snapshot()
            except Exception:
                self.disconnect()
                raise

    def _receive(self):
        try:
            while not self.stop.is_set():
                try:
                    raw = self.ws.recv(timeout=1)
                except TimeoutError:
                    continue
                packets = json.loads(raw)
                for packet in packets if isinstance(packets, list) else [packets]:
                    if self.protocol.name(packet.get('pid')) == 'conn_ping':
                        self.ws.send(json.dumps(self.protocol.packet('conn_pong')))
                    if self.protocol.name(packet.get('pid')) == 'server_join_reply' and packet.get('you_can_join'):
                        # The server gates web-only buildability/economy packets
                        # on this explicit client-info handshake, not capability alone.
                        self.ws.send(json.dumps(self.protocol.packet('client_info', gui=8,
                            emerg_version=0, distribution='freeciv-agent-harness')))
                    self.state.apply(packet)
        except (ConnectionClosed, OSError, ValueError) as error:
            if not self.stop.is_set():
                self.error = str(error)
                self.events.emit('disconnected', reason=self.error)
        finally:
            with self.state.condition:
                self.state.joined = False
                self.state.condition.notify_all()

    def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        with self.state.condition:
            while not predicate():
                if self.error:
                    raise ConnectionError(self.error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Server did not confirm the expected update; do not retry blindly')
                self.state.condition.wait(min(remaining, 0.5))

    def request(self, name, fields, predicate=None, timeout=10):
        """Serialize mutations; writes alone are never reported as confirmed success."""
        with self.action_lock:
            if not self.ws or not self.state.joined:
                raise ConnectionError('Not connected; call freeciv_connect first')
            with self.state.condition:
                processing = self.state.processing
                revision = self.state.revision
                cursor = self.events.cursor
            self.ws.send(json.dumps(self.protocol.packet(name, **fields)))
            confirmed = True
            try:
                self.wait(lambda: self.state.processing > processing and (predicate is None or predicate()), timeout)
            except TimeoutError:
                confirmed = False
            messages = self.events.read(after=cursor, kinds=['chat', 'message'])['events']
            result = {'request': name, 'confirmed': confirmed,
                      'confirmation': 'expected_state' if predicate and confirmed else ('processing_finished' if confirmed else 'unconfirmed'),
                      'state_changed': self.state.revision > revision, 'messages': messages,
                      'event_cursor': self.events.cursor}
            # A processing boundary means the server handled the request, not that
            # game rules allowed it. Always retain server messages for the caller.
            self.events.emit('action', **result)
            return result

    def disconnect(self):
        self.stop.set()
        if self.ws:
            self.ws.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=4)
        self.ws = None
        self.thread = None
        with self.state.condition:
            self.state.joined = False
            self.state.condition.notify_all()
        return {'disconnected': True}
