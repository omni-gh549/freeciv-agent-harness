import copy
import re

from .client import GameClient
from .protocol import bitset_has


DIRS = {'NW': 0, 'N': 1, 'NE': 2, 'W': 3, 'E': 4, 'SW': 5, 'S': 6, 'SE': 7}
DELTAS = {0: (-1, -1), 1: (0, -1), 2: (1, -1), 3: (-1, 0), 4: (1, 0), 5: (-1, 1), 6: (0, 1), 7: (1, 1)}


class GameService:
    def __init__(self, client=None):
        self.client = client or GameClient()

    @property
    def state(self):
        return self.client.state

    def call(self, name, args=None):
        from .tools import validate
        args = {} if args is None else args
        validate(name, args)
        method = getattr(self, name.removeprefix('freeciv_'), None) if name.startswith('freeciv_') else None
        if name not in TOOL_NAMES or method is None:
            raise ValueError('Unknown tool: ' + name)
        # Mutating and connection operations share a lock, including their read/
        # validate phase, to prevent duplicate concurrent end-turn calls.
        if name in READ_TOOLS:
            return method(**args)
        with self.client.action_lock:
            return method(**args)

    def connect(self, url='http://localhost:8080', name='Agent', game_port=6001, password=None):
        if self.state.joined:
            self.disconnect()
        return self.client.connect(url, name, game_port, password)

    def disconnect(self, save=True):
        saved = None
        try:
            if save and self.state.joined and self.state.running and self.state.player_id is not None:
                saved = self.save()
        finally:
            result = self.client.disconnect()
        if saved is not None:
            result['save_request'] = saved
        return result

    def status(self):
        return self.state.snapshot()

    def events(self, after=0, kinds=None, timeout=0, limit=100):
        return self.client.events.read(after, kinds, timeout, limit)

    def _active(self, expected_turn=None):
        if not self.state.joined or self.state.player_id is None:
            raise ValueError('This connection does not control a player')
        turn = self.state.game.get('turn')
        if expected_turn is not None and expected_turn != turn:
            raise ValueError(f'Stale turn: expected {expected_turn}, current {turn}')
        return self.state.player_id

    def units(self):
        with self.state.condition:
            me = self.state.player_id
            return copy.deepcopy([dict(u, type_name=self.state.tables['unit_types'].get(u.get('type'), {}).get('name'))
                                  for u in self.state.tables['units'].values() if me is not None and u.get('owner') == me])

    def cities(self):
        with self.state.condition:
            me = self.state.player_id
            result = []
            for c in self.state.tables['cities'].values():
                if me is None or c.get('owner') != me:
                    continue
                c = copy.deepcopy(c)
                c['buildable_units'] = [{'id': i, 'name': t.get('name')} for i, t in self.state.tables['unit_types'].items() if bitset_has(c.get('can_build_unit'), i)]
                c['buildable_buildings'] = [{'id': i, 'name': t.get('name')} for i, t in self.state.tables['buildings'].items() if bitset_has(c.get('can_build_improvement'), i)]
                result.append(c)
            return result

    def map(self, x=0, y=0, width=20, height=20):
        if any(not isinstance(v, int) for v in (x, y, width, height)) or not 1 <= width <= 80 or not 1 <= height <= 80:
            raise ValueError('Map bounds require integers and width/height in 1..80')
        with self.state.condition:
            size_x, size_y = self.state.map.get('xsize', 0), self.state.map.get('ysize', 0)
            result = []
            for t in self.state.tables['tiles'].values():
                if not size_x or t.get('known', 0) <= 0:
                    continue
                tx, ty = t['tile'] % size_x, t['tile'] // size_x
                if x <= tx < x + width and y <= ty < y + height:
                    result.append(dict(copy.deepcopy(t), x=tx, y=ty,
                                       terrain_name=self.state.tables['terrains'].get(t.get('terrain'), {}).get('name')))
            return {'xsize': size_x, 'ysize': size_y, 'tiles': result, 'fog_of_war': True}

    def ruleset(self, table, query=None):
        if table not in ('techs', 'unit_types', 'buildings', 'terrains', 'extras', 'nations', 'governments', 'actions'):
            raise ValueError('Unknown ruleset table')
        with self.state.condition:
            values = list(self.state.tables[table].values())
            if query:
                values = [v for v in values if any(query.casefold() in str(v.get(k, '')).casefold()
                          for k in ('name', 'ui_name', 'adjective', 'rule_name', 'noun_plural'))]
            return copy.deepcopy(values)

    def end_turn(self, expected_turn):
        me = self._active(expected_turn)
        if self.state.tables['players'][me].get('phase_done'):
            return {'already_done': True, 'turn': expected_turn}
        return self.client.request('player_phase_done', {'turn': expected_turn},
            lambda: self.state.game.get('turn', -1) > expected_turn or bool(self.state.tables['players'].get(me, {}).get('phase_done')))

    def chat(self, message):
        if not isinstance(message, str) or not message.strip() or len(message) > 350 or '\n' in message or '\r' in message:
            raise ValueError('Chat requires 1..350 characters on one line')
        if message.lstrip().startswith('/'):
            raise ValueError('Chat cannot run server commands; use save/load/lobby tools')
        cursor = self.client.events.cursor
        return self.client.request('chat_msg_req', {'message': message},
            lambda: any(e['kind'] == 'chat' and message in e.get('text', '') for e in self.client.events.read(after=cursor)['events']))

    def save(self):
        return self.client.request('chat_msg_req', {'message': '/save'})

    def load(self, filename):
        if self.state.running:
            raise ValueError('Load is restricted to the pregame lobby; never replace a live game')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', filename):
            raise ValueError('Use a save basename without path or .sav/compression extension')
        cursor = self.client.events.cursor
        return self.client.request('chat_msg_req', {'message': '/load ' + filename},
            lambda: any(e.get('kind') == 'load' and e.get('success') for e in self.client.events.read(after=cursor)['events']))

    def lobby(self, ready=True):
        me = self._active()
        if self.state.running:
            raise ValueError('Game is already running')
        return self.client.request('player_ready', {'player_no': me, 'is_ready': bool(ready)},
                                   lambda: self.state.running or self.state.tables['players'].get(me, {}).get('is_ready') == bool(ready))

    def research(self, tech_id, goal=False, expected_turn=None):
        self._active(expected_turn)
        if tech_id not in self.state.tables['techs']:
            raise ValueError('Unknown technology; inspect the ruleset first')
        field = 'tech_goal' if goal else 'researching'
        return self.client.request('player_tech_goal' if goal else 'player_research', {'tech': tech_id},
            lambda: self.state.tables['research'].get(self.state.player_id, {}).get(field) == tech_id)

    def production(self, city_id, kind, target_id, expected_turn=None):
        self._active(expected_turn)
        city = self.state.owned('cities', city_id)
        if kind not in ('unit', 'building'):
            raise ValueError('kind must be unit or building')
        field = 'can_build_unit' if kind == 'unit' else 'can_build_improvement'
        if not bitset_has(city.get(field), target_id):
            raise ValueError('Server has not reported this production as buildable here')
        wire_kind = 61 if kind == 'unit' else 20
        return self.client.request('city_change', {'city_id': city_id, 'production_kind': wire_kind, 'production_value': target_id},
            lambda: self.state.tables['cities'].get(city_id, {}).get('production_kind') == wire_kind and self.state.tables['cities'].get(city_id, {}).get('production_value') == target_id)

    def unit_activity(self, unit_id, activity, expected_turn=None):
        self._active(expected_turn)
        self.state.owned('units', unit_id)
        if activity in ('auto_work', 'auto_explore'):
            agent = 1 if activity == 'auto_work' else 2
            return self.client.request('unit_server_side_agent_set', {'unit_id': unit_id, 'agent': agent},
                lambda: self.state.tables['units'].get(unit_id, {}).get('ssa_controller') == agent)
        activities = {'idle': 0, 'sentry': 5, 'fortify': 10}
        if activity not in activities:
            raise ValueError('activity must be idle, sentry, fortify, auto_work, or auto_explore')
        return self.client.request('unit_change_activity', {'unit_id': unit_id, 'activity': activities[activity], 'target': -1},
            lambda: self.state.tables['units'].get(unit_id, {}).get('activity') in ([4, 10] if activity == 'fortify' else [activities[activity]]))

    def move(self, unit_id, direction, expected_turn=None):
        self._active(expected_turn)
        unit = self.state.owned('units', unit_id)
        if direction.upper() not in DIRS:
            raise ValueError('Direction must be N, NE, E, SE, S, SW, W, or NW')
        if unit.get('movesleft', 0) <= 0:
            raise ValueError('Unit has no moves left')
        width, height = self.state.map['xsize'], self.state.map['ysize']
        dx, dy = DELTAS[DIRS[direction.upper()]]
        x, y = unit['tile'] % width + dx, unit['tile'] // width + dy
        wrap = self.state.map.get('wrap_id', 1)
        if wrap & 1:
            x %= width
        if wrap & 2:
            y %= height
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError('Destination is outside the map')
        target = y * width + x
        # Movement may explore unknown tiles. Cache reads still expose only
        # server-supplied known tiles; no map reconstruction or server access.
        order = {'order': 3, 'dir': DIRS[direction.upper()], 'activity': 16,
                 'target': 0, 'sub_target': 0, 'action': 0}
        return self.client.request('unit_orders', {'unit_id': unit_id, 'src_tile': unit['tile'],
            'length': 1, 'repeat': False, 'vigilant': False, 'orders': [order], 'dest_tile': target},
            lambda: unit_id not in self.state.tables['units'] or self.state.tables['units'][unit_id].get('tile') != unit['tile'] or self.state.tables['units'][unit_id].get('movesleft') != unit.get('movesleft'))

    def found_city(self, unit_id, name, expected_turn=None):
        self._active(expected_turn)
        unit = self.state.owned('units', unit_id)
        if unit.get('movesleft', 0) <= 0:
            raise ValueError('Unit has no moves left')
        if any(c.get('tile') == unit['tile'] for c in self.state.tables['cities'].values()):
            raise ValueError('A known city already occupies this tile')
        if not isinstance(name, str) or not name.strip() or len(name) > 48 or any(ord(c) < 32 for c in name):
            raise ValueError('City name requires 1..48 printable characters')
        # Action 27 is a versioned protocol-profile fact; the server decides
        # whether this unit and location can legally found a city.
        return self.client.request('unit_do_action', {'actor_id': unit_id, 'target_id': unit['tile'],
            'sub_tgt_id': 0, 'name': name, 'action_type': 27},
            lambda: any(c.get('owner') == self.state.player_id and c.get('name') == name and c.get('tile') == unit['tile'] for c in self.state.tables['cities'].values()))

    def goto(self, unit_id, x, y, expected_turn=None):
        self._active(expected_turn)
        unit = self.state.owned('units', unit_id)
        width, height = self.state.map['xsize'], self.state.map['ysize']
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError('Destination is outside the map')
        goal = y * width + x
        if goal == unit['tile']:
            return {'already_there': True}
        with self.state.condition:
            self.state.tables['paths'].pop(unit_id, None)
        path_result = self.client.request('web_goto_path_req', {'unit_id': unit_id, 'goal': goal},
            lambda: unit_id in self.state.tables['paths'])
        if not path_result['confirmed']:
            return path_result
        with self.state.condition:
            path = copy.deepcopy(self.state.tables['paths'][unit_id])
        directions = path.get('dir', [])[:path.get('length', 0)]
        if not directions or len(directions) > 1000:
            raise ValueError('Server did not return a usable path')
        orders = [{'order': 3, 'dir': d, 'activity': 16, 'target': 0, 'sub_target': 0, 'action': 0} for d in directions]
        result = self.client.request('unit_orders', {'unit_id': unit_id, 'src_tile': unit['tile'], 'length': len(orders),
            'repeat': False, 'vigilant': False, 'orders': orders, 'dest_tile': path['dest']},
            lambda: unit_id not in self.state.tables['units'] or self.state.tables['units'][unit_id].get('has_orders') or self.state.tables['units'][unit_id].get('tile') != unit['tile'])
        result['path'] = {'steps': len(orders), 'turns': path.get('turns'), 'destination': path['dest']}
        return result

    def action_options(self, unit_id, tile_id, target_unit_id=0):
        self._active()
        self.state.owned('units', unit_id)
        with self.state.condition:
            self.state.tables['action_options'].pop(unit_id, None)
        result = self.client.request('unit_get_actions', {'actor_unit_id': unit_id,
            'target_unit_id': target_unit_id, 'target_tile_id': tile_id, 'target_extra_id': -1, 'request_kind': 0},
            lambda: unit_id in self.state.tables['action_options'])
        with self.state.condition:
            result['options'] = copy.deepcopy(self.state.tables['action_options'].get(unit_id))
        return result

    def do_action(self, unit_id, action_id, target_id, sub_target_id=0, name='', expected_turn=None):
        self._active(expected_turn)
        self.state.owned('units', unit_id)
        if action_id not in self.state.tables['actions']:
            raise ValueError('Unknown action; inspect action_options and ruleset first')
        return self.client.request('unit_do_action', {'actor_id': unit_id, 'target_id': target_id,
            'sub_tgt_id': sub_target_id, 'name': name, 'action_type': action_id})

    def rates(self, tax, science, luxury, expected_turn=None):
        me = self._active(expected_turn)
        if tax + science + luxury != 100 or any(v % 10 for v in (tax, science, luxury)):
            raise ValueError('Rates must total 100 and be multiples of 10; server government limits still apply')
        return self.client.request('player_rates', {'tax': tax, 'science': science, 'luxury': luxury},
            lambda: all(self.state.tables['players'][me].get(k) == v for k, v in [('tax', tax), ('science', science), ('luxury', luxury)]))

    def government(self, government_id, expected_turn=None):
        me = self._active(expected_turn)
        if government_id not in self.state.tables['governments']:
            raise ValueError('Unknown government')
        return self.client.request('player_change_government', {'government': government_id},
            lambda: self.state.tables['players'][me].get('government') == government_id or self.state.tables['players'][me].get('target_government') == government_id)

    def city_buy(self, city_id, expected_turn=None):
        self._active(expected_turn)
        self.state.owned('cities', city_id)
        return self.client.request('city_buy', {'city_id': city_id})

    def nation(self, nation_id, leader_name, male=True):
        me = self._active()
        if self.state.running:
            raise ValueError('Nation selection is restricted to the lobby')
        nation = self.state.tables['nations'].get(nation_id)
        if nation is None or not leader_name.strip() or len(leader_name) > 48 or any(ord(c) < 32 for c in leader_name):
            raise ValueError('Choose a known nation and a printable leader name of 1..48 characters')
        return self.client.request('nation_select_req', {'player_no': me, 'nation_no': nation_id,
            'is_male': male, 'name': leader_name, 'style': nation.get('style', 0)},
            lambda: self.state.tables['players'][me].get('nation') == nation_id)

    def setting(self, name, value):
        allowed = {'timeout', 'aifill', 'size', 'topology', 'wrap', 'minplayers', 'maxplayers', 'generator', 'seed', 'mapseed'}
        if name not in allowed:
            raise ValueError('Unsupported setting; only timing and basic lobby/map setup are exposed')
        if self.state.running and name != 'timeout':
            raise ValueError('Only timeout may be changed during a running game')
        if not re.fullmatch(r'[A-Za-z0-9_+|\-]{1,80}', value):
            raise ValueError('Invalid setting value')
        return self.client.request('chat_msg_req', {'message': '/set ' + name + ' ' + value})

    def vote(self, approve):
        return self.client.request('chat_msg_req', {'message': '/vote ' + ('yes' if approve else 'no')})


READ_TOOLS = {'freeciv_status', 'freeciv_units', 'freeciv_cities', 'freeciv_map', 'freeciv_ruleset', 'freeciv_events'}
TOOL_NAMES = READ_TOOLS | {'freeciv_connect', 'freeciv_disconnect', 'freeciv_end_turn', 'freeciv_chat', 'freeciv_save', 'freeciv_load', 'freeciv_lobby', 'freeciv_research', 'freeciv_production', 'freeciv_unit_activity', 'freeciv_move', 'freeciv_found_city', 'freeciv_goto', 'freeciv_action_options', 'freeciv_do_action', 'freeciv_rates', 'freeciv_government', 'freeciv_city_buy', 'freeciv_nation', 'freeciv_setting', 'freeciv_vote'}
