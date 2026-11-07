"""Runnable runner-protocol example; replace with your own model/agent adapter.

This example deliberately makes no strategic moves: it acknowledges chat and
ends its own turn. It is a transport demonstration, not an intelligent player.
"""
import json
import sys

observation = json.load(sys.stdin)
event = observation['event']
actions = []
if event['kind'] == 'chat':
    actions.append({'name': 'freeciv_chat', 'arguments': {'message': 'Message received by the standalone harness.'}})
elif not observation['status']['me'].get('phase_done'):
    actions.append({'name': 'freeciv_end_turn', 'arguments': {'expected_turn': observation['status']['game']['turn']}})
print(json.dumps({'actions': actions}))
