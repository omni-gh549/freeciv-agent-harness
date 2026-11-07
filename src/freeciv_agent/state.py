import copy
import html
import re
import threading


TABLES = {
    'player_info': ('players', 'playerno'), 'web_player_info_addition': ('players', 'playerno'),
    'city_info': ('cities', 'id'), 'city_short_info': ('cities', 'id'),
    'web_city_info_addition': ('cities', 'id'), 'unit_info': ('units', 'id'),
    'unit_short_info': ('units', 'id'), 'tile_info': ('tiles', 'tile'),
    'conn_info': ('connections', 'id'), 'research_info': ('research', 'id'),
    'ruleset_unit': ('unit_types', 'id'), 'web_ruleset_unit_addition': ('unit_types', 'id'),
    'ruleset_tech': ('techs', 'id'), 'ruleset_building': ('buildings', 'id'),
    'ruleset_terrain': ('terrains', 'id'), 'ruleset_extra': ('extras', 'id'),
    'ruleset_nation': ('nations', 'id'), 'ruleset_government': ('governments', 'id'),
    'ruleset_action': ('actions', 'id'),
    'web_goto_path': ('paths', 'unit_id'), 'unit_actions': ('action_options', 'actor_unit_id'),
}


class GameState:
    def __init__(self, protocol, events):
        self.protocol, self.events = protocol, events
        self.condition = threading.Condition(threading.RLock())
        self.reset()

    def reset(self):
        with self.condition:
            self.tables = {table: {} for table, _ in TABLES.values()}
            self.game, self.map, self.rules = {}, {}, {}
            self.conn_id = None
            self.joined = False
            self.rejected = None
            self.running = False
            self.processing = 0
            self.revision = 0
            self.last_turn_event = None
            self.condition.notify_all()

    @property
    def player_id(self):
        conn = self.tables['connections'].get(self.conn_id, {})
        value = conn.get('player_num')
        return value if value in self.tables['players'] and not conn.get('observer') else None

    def apply(self, packet):
        name = self.protocol.name(packet.get('pid'))
        with self.condition:
            self.revision += 1
            if name in TABLES:
                table, key = TABLES[name]
                if key in packet:
                    self.tables[table].setdefault(packet[key], {}).update(packet)
                if name == 'conn_info' and not packet.get('used', True):
                    self.tables['connections'].pop(packet['id'], None)
            elif name in ('unit_remove', 'city_remove', 'player_remove'):
                table = {'unit_remove': 'units', 'city_remove': 'cities', 'player_remove': 'players'}[name]
                self.tables[table].pop(packet.get('unit_id', packet.get('city_id', packet.get('playerno'))), None)
            elif name in ('game_info', 'calendar_info', 'new_year'):
                self.game.update(packet)
            elif name == 'map_info':
                self.map.update(packet)
            elif name == 'ruleset_control':
                self.rules.update(packet)
            elif name == 'server_join_reply':
                self.joined = bool(packet.get('you_can_join'))
                self.conn_id = packet.get('conn_id')
                if not self.joined:
                    self.rejected = packet.get('message', 'Login rejected')
            elif name == 'processing_finished':
                self.processing += 1
            elif name == 'game_load':
                self.events.emit('load', success=packet.get('load_successful'), filename=packet.get('load_filename'))
            elif name == 'start_phase':
                self.running = True
                self.game['phase'] = packet.get('phase')
            elif name == 'begin_turn':
                self.running = True
                turn = self.game.get('turn')
                if turn is not None and turn != self.last_turn_event:
                    self.last_turn_event = turn
                    self.events.emit('turn', turn=turn, year=self.game.get('year'))
            elif name == 'chat_msg':
                conn = self.tables['connections'].get(packet.get('conn_id'), {})
                text = html.unescape(re.sub('<[^>]+>', '', str(packet.get('message', ''))))
                kind = 'chat' if conn.get('username') else 'message'
                self.events.emit(kind, sender=conn.get('username'), text=text,
                                 event_code=packet.get('event'), turn=self.game.get('turn'))
            elif name == 'server_shutdown':
                self.events.emit('shutdown')
            self.condition.notify_all()

    def owned(self, table, entity_id):
        with self.condition:
            entity = self.tables[table].get(entity_id)
            if self.player_id is None or not entity or entity.get('owner') != self.player_id:
                raise ValueError(f'{table[:-1].capitalize()} {entity_id} is not controlled by this connection')
            return copy.deepcopy(entity)

    def snapshot(self):
        with self.condition:
            me = self.tables['players'].get(self.player_id, {})
            return copy.deepcopy({
                'joined': self.joined, 'running': self.running, 'revision': self.revision,
                'game': {k: self.game.get(k) for k in ('turn', 'year', 'phase', 'timeout')},
                'map': self.map, 'player_id': self.player_id,
                'me': {k: me.get(k) for k in ('name', 'username', 'nation', 'government', 'gold', 'expected_income',
                       'tax', 'science', 'luxury', 'phase_done', 'is_connected', 'team')},
                'research': self.tables['research'].get(self.player_id, {}),
                'players': [{k: p.get(k) for k in ('playerno', 'name', 'nation', 'team', 'is_connected',
                             'phase_done', 'is_alive')} for p in self.tables['players'].values()],
                'counts': {'own_cities': sum(c.get('owner') == self.player_id for c in self.tables['cities'].values()) if self.player_id is not None else 0,
                           'own_units': sum(u.get('owner') == self.player_id for u in self.tables['units'].values()) if self.player_id is not None else 0,
                           'known_tiles': sum(t.get('known', 0) > 0 for t in self.tables['tiles'].values())},
                'event_cursor': self.events.cursor,
            })
