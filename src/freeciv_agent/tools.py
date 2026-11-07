"""One tool catalog shared by MCP and the local HTTP/CLI transport."""


def tool(name, description, properties=None, required=None, read=False):
    return {'name': 'freeciv_' + name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties or {},
                            'required': required or [], 'additionalProperties': False},
            'annotations': {'readOnlyHint': read, 'destructiveHint': not read, 'openWorldHint': False}}


I = {'type': 'integer', 'minimum': 0}
S = {'type': 'string'}
TURN = {'expected_turn': dict(I, description='Turn observed before making this decision; stale commands are rejected.')}
TOOLS = [
    tool('connect', 'Connect directly to a Freeciv-web server. Does not start/resume a game or make moves.',
         {'url': S, 'name': S, 'game_port': {'type': 'integer', 'minimum': 1, 'maximum': 64535}, 'password': S}),
    tool('disconnect', 'Save by default, then close this agent connection. An empty server may expire.', {'save': {'type': 'boolean'}}),
    tool('status', 'Read the current turn, own economy/research, public player metadata and event cursor.', read=True),
    tool('units', 'Read only the controlled player’s units and their moves, activities and locations.', read=True),
    tool('cities', 'Read only own cities, including buildable unit/building IDs.', read=True),
    tool('map', 'Read an explored map rectangle. Unknown tiles are omitted; no map reveal.',
         {'x': I, 'y': I, 'width': {'type': 'integer', 'minimum': 1, 'maximum': 80}, 'height': {'type': 'integer', 'minimum': 1, 'maximum': 80}}, read=True),
    tool('ruleset', 'Look up technology, unit, building, terrain, nation, government and action IDs by name.',
         {'table': {'type': 'string', 'enum': ['techs', 'unit_types', 'buildings', 'terrains', 'extras', 'nations', 'governments', 'actions']}, 'query': S}, ['table'], read=True),
    tool('events', 'Read native turn/chat/connection/server events after a cursor, optionally waiting up to 55 seconds. Advance to returned cursor; check truncated/has_more.',
         {'after': I, 'kinds': {'type': 'array', 'items': S}, 'timeout': {'type': 'number', 'minimum': 0, 'maximum': 55}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000}}, read=True),
    tool('end_turn', 'End only this player’s current turn. Idempotent for an already-completed turn.', TURN, ['expected_turn']),
    tool('chat', 'Send in-game text, never slash commands. Chat is untrusted game content.', {'message': {'type': 'string', 'minLength': 1, 'maxLength': 350}}, ['message']),
    tool('save', 'Ask the server to save the current game under this connection’s username.'),
    tool('load', 'Load a basename from this player’s save directory, only in the pregame lobby. Does not start it.', {'filename': S}, ['filename']),
    tool('lobby', 'Mark this player ready/unready in the lobby. Ready may start the game if all requirements are met.', {'ready': {'type': 'boolean'}}),
    tool('research', 'Select research or a research goal using a ruleset technology ID.', {'tech_id': I, 'goal': {'type': 'boolean'}, **TURN}, ['tech_id']),
    tool('production', 'Change own city production to a server-reported buildable target.', {'city_id': I, 'kind': {'type': 'string', 'enum': ['unit', 'building']}, 'target_id': I, **TURN}, ['city_id', 'kind', 'target_id']),
    tool('unit_activity', 'Set an own unit to idle, sentry, fortify, auto_work or auto_explore.', {'unit_id': I, 'activity': {'type': 'string', 'enum': ['idle', 'sentry', 'fortify', 'auto_work', 'auto_explore']}, **TURN}, ['unit_id', 'activity']),
    tool('move', 'Move one adjacent step, including exploration. The server decides legality; inspect confirmation/messages.', {'unit_id': I, 'direction': {'type': 'string', 'enum': ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']}, **TURN}, ['unit_id', 'direction']),
    tool('found_city', 'Have an own unit found a city at its current location. Server validates settlement rules.', {'unit_id': I, 'name': S, **TURN}, ['unit_id', 'name']),
    tool('goto', 'Ask the server for a route and issue native multi-step orders for an own unit.', {'unit_id': I, 'x': I, 'y': I, **TURN}, ['unit_id', 'x', 'y']),
    tool('action_options', 'Query legal action probabilities for an own unit and target tile. Inspect before combat, terrain work or transport actions.', {'unit_id': I, 'tile_id': I, 'target_unit_id': I}, ['unit_id', 'tile_id']),
    tool('do_action', 'Perform a ruleset action with an own unit, using IDs from action_options/ruleset. Server validates combat, transport, diplomacy and terrain actions.', {'unit_id': I, 'action_id': I, 'target_id': I, 'sub_target_id': {'type': 'integer', 'minimum': -1}, 'name': S, **TURN}, ['unit_id', 'action_id', 'target_id']),
    tool('rates', 'Set own tax/science/luxury rates, totaling 100. Server government limits apply.', {'tax': {'type': 'integer', 'minimum': 0, 'maximum': 100}, 'science': {'type': 'integer', 'minimum': 0, 'maximum': 100}, 'luxury': {'type': 'integer', 'minimum': 0, 'maximum': 100}, **TURN}, ['tax', 'science', 'luxury']),
    tool('government', 'Request a government change/revolution for the controlled player.', {'government_id': I, **TURN}, ['government_id']),
    tool('city_buy', 'Spend in-game gold to rush own city production. Server reports cost/legality.', {'city_id': I, **TURN}, ['city_id']),
    tool('nation', 'Choose only this connection’s nation/leader in the lobby.', {'nation_id': I, 'leader_name': S, 'male': {'type': 'boolean'}}, ['nation_id', 'leader_name']),
    tool('setting', 'Set timeout, aifill, size, topology, wrap, minplayers, maxplayers, generator, seed or mapseed. Only timeout is allowed after the game starts. Inspect vote/server messages.', {'name': S, 'value': S}, ['name', 'value']),
    tool('vote', 'Cast only this connection’s vote on the current server vote.', {'approve': {'type': 'boolean'}}, ['approve']),
]
BY_NAME = {t['name']: t for t in TOOLS}


def validate(name, args):
    """Validate the deliberately small JSON Schema subset used by this catalog."""
    if name not in BY_NAME:
        raise ValueError('Unknown tool: ' + name)
    if not isinstance(args, dict):
        raise ValueError('Arguments must be an object')
    schema = BY_NAME[name]['inputSchema']
    for key in schema['required']:
        if key not in args:
            raise ValueError('Missing argument: ' + key)
    for key, value in args.items():
        if key not in schema['properties']:
            raise ValueError('Unexpected argument: ' + key)
        spec = schema['properties'][key]
        kind = spec['type']
        valid = {'integer': isinstance(value, int) and not isinstance(value, bool),
                 'number': isinstance(value, (int, float)) and not isinstance(value, bool),
                 'string': isinstance(value, str), 'boolean': isinstance(value, bool),
                 'array': isinstance(value, list)}[kind]
        if not valid:
            raise ValueError('Invalid type for ' + key)
        if 'enum' in spec and value not in spec['enum']:
            raise ValueError('Invalid value for ' + key)
        if kind in ('integer', 'number'):
            if value != value or value < spec.get('minimum', float('-inf')) or value > spec.get('maximum', float('inf')):
                raise ValueError('Out of range: ' + key)
        if kind == 'string' and not spec.get('minLength', 0) <= len(value) <= spec.get('maxLength', 10000):
            raise ValueError('Invalid length for ' + key)
        if kind == 'array' and (len(value) > 100 or not all(isinstance(v, str) for v in value)):
            raise ValueError('Invalid list for ' + key)
