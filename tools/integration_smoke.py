"""Destructive to its NEW test game only. Requires a labeled disposable container."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from freeciv_agent.service import GameService, DIRS, DELTAS


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', required=True)
    p.add_argument('--container', required=True)
    a = p.parse_args()
    label = subprocess.check_output(['docker', 'inspect', a.container, '--format', '{{index .Config.Labels "freeciv-agent-harness.test"}}'], text=True).strip()
    if label != 'true':
        raise SystemExit('Refusing: container is not explicitly labeled as disposable harness test data')
    published = subprocess.check_output(['docker', 'port', a.container, '80/tcp'], text=True).strip()
    if a.url.removeprefix('http://').removeprefix('https://').replace('localhost', '127.0.0.1') != published:
        raise SystemExit('Refusing: URL does not match the disposable container port')
    service = GameService()
    peer = GameService()
    checks = []

    def check(label, predicate):
        if not predicate:
            raise AssertionError(label)
        checks.append(label)
        print(json.dumps({'passed': label}), flush=True)

    try:
        first = service.connect(a.url, 'HarnessTest', 6001)
        check('native connect and own-player assignment', first['joined'] and first['player_id'] is not None)
        if first['running'] or first['game']['turn'] != 0:
            raise RuntimeError('Refusing to alter an already-running game')
        # Administrative setup is confined to this explicitly disposable test.
        for name, value in [('minplayers', '1'), ('aifill', '2'), ('size', '1'), ('timeout', '0')]:
            result = service.setting(name, value)
            print(json.dumps({'setup': name, 'result': result}), flush=True)
        service.client.request('chat_msg_req', {'message': '/set autotoggle disabled'})
        check('typed lobby settings', service.status()['game']['timeout'] == 0 and len(service.status()['players']) == 2)
        nation = service.ruleset('nations', 'Japanese')[0]
        check('native nation selection', service.nation(nation['id'], 'HarnessTest')['confirmed'])
        check('lobby ready starts game', service.lobby()['confirmed'])
        service.client.wait(lambda: bool(service.state.map) and any('Settler' in u.get('type_name', '') and u.get('movesleft', 0) > 0 for u in service.units()), timeout=20)
        status = service.status()
        check('game state and explored map received', status['running'] and status['counts']['known_tiles'] > 0)
        check('map does not include unknown tiles', all(t['known'] > 0 for t in service.map(0, 0, 80, 80)['tiles']))
        check('own units only', all(u['owner'] == status['player_id'] for u in service.units()))
        settler = next(u for u in service.units() if 'Settler' in u.get('type_name', '') and u.get('movesleft', 0) > 0)
        check('found a city without browser', service.found_city(settler['id'], 'HarnessCity', status['game']['turn'])['confirmed'])
        city = next(c for c in service.cities() if c['name'] == 'HarnessCity')
        target = city['buildable_units'][0]
        check('city production acknowledged', service.production(city['id'], 'unit', target['id'], status['game']['turn'])['confirmed'])
        check('tax/science rates acknowledged', service.rates(40, 60, 0, status['game']['turn'])['confirmed'])
        worker = service.units()[0]
        check('native action options', service.action_options(worker['id'], worker['tile'])['options'] is not None)
        check('sentry activity acknowledged', service.unit_activity(worker['id'], 'sentry', status['game']['turn'])['confirmed'])
        check('idle activity acknowledged', service.unit_activity(worker['id'], 'idle', status['game']['turn'])['confirmed'])
        research = service.status()['research']
        known = research.get('inventions', '')
        available = [t for t in service.ruleset('techs') if t['id'] < len(known) and known[t['id']] == '1']
        if available:
            check('research selection acknowledged', service.research(available[0]['id'], expected_turn=status['game']['turn'])['confirmed'])
        mover = next((u for u in service.units() if u.get('movesleft', 0) > 0 and u['id'] != settler['id']), None)
        if mover:
            width, height = service.state.map['xsize'], service.state.map['ysize']
            moved = False
            for direction, number in DIRS.items():
                dx, dy = DELTAS[number]
                x, y = (mover['tile'] % width + dx) % width, mover['tile'] // width + dy
                tile = service.state.tables['tiles'].get(y * width + x, {}) if 0 <= y < height else {}
                terrain = service.state.tables['terrains'].get(tile.get('terrain'), {}).get('name', '')
                if tile.get('known', 0) > 0 and terrain and terrain not in ('Ocean', 'Deep Ocean', 'Lake'):
                    moved = service.move(mover['id'], direction, status['game']['turn'])['confirmed']
                    if moved:
                        break
            check('native unit movement acknowledged', moved)
        cursor = service.client.events.cursor
        check('native chat delivery', service.chat('Harness live chat self-test')['confirmed'])
        events = service.events(cursor, ['chat'], 1)
        check('chat event cursor delivery', any('self-test' in e.get('text', '') for e in events['events']))
        peer.connect(a.url, 'HarnessPeer', 6001)
        check('second client receives chat', service.chat('Peer delivery check')['confirmed'])
        peer.client.wait(lambda: any('Peer delivery check' in e.get('text', '') for e in peer.events()['events']))
        check('independent peer confirmation', True)
        peer.disconnect()
        cursor = service.client.events.cursor
        old_turn = service.status()['game']['turn']
        check('end turn confirmed', service.end_turn(old_turn)['confirmed'])
        service.client.wait(lambda: service.status()['game']['turn'] > old_turn, timeout=20)
        check('real next-turn event', any(e['kind'] == 'turn' and e.get('turn', -1) > old_turn for e in service.events(cursor)['events']))
        mover = next(u for u in service.units() if u.get('movesleft', 0) > 0)
        width = service.state.map['xsize']
        home = service.cities()[0]['tile']
        if mover['tile'] != home:
            route = service.goto(mover['id'], home % width, home // width, service.status()['game']['turn'])
            check('server pathfinding and goto orders', route['confirmed'])
        try:
            service.end_turn(old_turn)
        except ValueError:
            check('stale-turn action rejected', True)
        else:
            raise AssertionError('Stale turn was accepted')
        from freeciv_agent.runner import run
        runner_output = []
        run(service, [sys.executable, str(Path(__file__).resolve().parents[1] / 'examples' / 'passive_agent.py')],
            max_events=1, output=lambda line: runner_output.append(json.loads(line)))
        check('standalone event runner invokes agent and ends turn',
              bool(runner_output) and any(a.get('name') == 'freeciv_end_turn' and a.get('result', {}).get('confirmed') for a in runner_output[0]['actions']))
        result = service.save()
        check('save request processed', result['confirmed'])
        time.sleep(1)
        saved = subprocess.check_output(['docker', 'exec', a.container, 'find', '/var/lib/tomcat10/webapps/data/savegames/HarnessTest', '-type', 'f'], text=True)
        check('save file independently exists', 'HarnessTest_T' in saved)
        service.disconnect()
        rejoined = service.connect(a.url, 'HarnessTest', 6001)
        check('rejoin restores own civilization', rejoined['me']['name'] == 'HarnessTest' and rejoined['counts']['own_cities'] > 0)
        check('web buildability survives rejoin', bool(service.cities()[0]['buildable_units']))
        print(json.dumps({'passed': len(checks), 'checks': checks, 'final_turn': rejoined['game']['turn']}, indent=2), flush=True)
    finally:
        peer.disconnect()
        service.disconnect()


if __name__ == '__main__':
    main()
