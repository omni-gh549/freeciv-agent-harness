import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def api_call(runtime_dir, name, arguments=None, path='/call'):
    runtime = json.loads((Path(runtime_dir) / 'connection.json').read_text(encoding='utf-8'))
    request = Request(runtime['url'] + path,
                      data=json.dumps({'name': name, 'arguments': arguments or {}}).encode(),
                      headers={'Authorization': 'Bearer ' + runtime['token'], 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=65) as response:
            value = json.load(response)
    except HTTPError as error:
        value = json.load(error)
        raise RuntimeError(value.get('error', str(error))) from error
    return value.get('result', value)


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description='Native Freeciv-web tools for agents; no browser or Codex-specific queue.')
    p.add_argument('--runtime-dir', type=Path, default=Path('runtime'), help='Local CLI daemon metadata; never commit this directory')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('mcp', help='Run an MCP stdio server; it connects to games only when asked')
    serve = commands.add_parser('serve', help='Run optional loopback API for CLI/scripts')
    serve.add_argument('--port', type=int, default=8788)
    serve.add_argument('--url', help='Optionally connect to this Freeciv-web origin')
    serve.add_argument('--name', default='Agent')
    serve.add_argument('--game-port', type=int, default=6001)
    connect = commands.add_parser('connect', help='Connect the CLI daemon to a game')
    connect.add_argument('--url', default='http://localhost:8080')
    connect.add_argument('--name', default='Agent')
    connect.add_argument('--game-port', type=int, default=6001)
    commands.add_parser('status')
    commands.add_parser('units')
    commands.add_parser('cities')
    commands.add_parser('disconnect')
    commands.add_parser('save')
    commands.add_parser('shutdown', help='Stop only the CLI daemon; game server stays running')
    end = commands.add_parser('end-turn')
    end.add_argument('--turn', type=int, required=True)
    chat = commands.add_parser('chat')
    chat.add_argument('message')
    call = commands.add_parser('call', help='Call any typed MCP tool using a JSON argument object')
    call.add_argument('name')
    call.add_argument('arguments', nargs='?', default='{}')
    events = commands.add_parser('events', help='Native event stream as newline-delimited JSON')
    events.add_argument('--after', type=int, default=0)
    events.add_argument('--kind', action='append')
    events.add_argument('--follow', action='store_true')
    runner = commands.add_parser('run-agent', help='Run a model-neutral executable directly on native turn/chat events')
    runner.add_argument('--agent-command-file', type=Path, required=True, help='JSON array: [executable, argument, ...]; no shell')
    runner.add_argument('--url', default='http://localhost:8080')
    runner.add_argument('--name', default='Agent')
    runner.add_argument('--game-port', type=int, default=6001)
    runner.add_argument('--max-events', type=int, default=0)
    runner.add_argument('--agent-timeout', type=int, default=120)
    play = commands.add_parser('play', help='Play autonomously using a Responses-compatible model')
    play.add_argument('--model-config', type=Path, required=True, help='Local JSON endpoint/model configuration; never commit credentials')
    play.add_argument('--url', default='http://localhost:8080')
    play.add_argument('--name', default='Agent')
    play.add_argument('--game-port', type=int, default=6001)
    play.add_argument('--max-events', type=int, default=0)
    play.add_argument('--agent-timeout', type=int, default=240)
    host = commands.add_parser('host', help='Manage an explicitly selected Freeciv-web Docker stack')
    hsub = host.add_subparsers(dest='host_command', required=True)
    start = hsub.add_parser('start')
    start.add_argument('--source', type=Path, required=True)
    start.add_argument('--container', default='freeciv-web')
    start.add_argument('--url', default='http://localhost:8080')
    start.add_argument('--restore', type=Path)
    start.add_argument('--name', default='Agent')
    stop = hsub.add_parser('stop')
    stop.add_argument('--container', default='freeciv-web')
    stop.add_argument('--backup-dir', type=Path)
    args = p.parse_args(argv)
    try:
        if args.command == 'mcp':
            from .mcp import run
            run()
            return
        if args.command == 'serve':
            from .client import GameClient
            from .events import EventLog
            from .http_api import make_server
            from .service import GameService
            service = GameService(GameClient(events=EventLog(journal=args.runtime_dir / 'events.jsonl')))
            if args.url:
                service.connect(args.url, args.name, args.game_port)
            server, runtime = make_server(service, args.runtime_dir, args.port)
            print(json.dumps({'api': runtime['url'], 'runtime_dir': str(args.runtime_dir.resolve())}), flush=True)
            try:
                server.serve_forever()
            finally:
                service.disconnect()
                server.server_close()
            return
        if args.command in ('run-agent', 'play'):
            from .runner import run
            from .service import GameService
            service = GameService()
            command = ([sys.executable, '-m', 'freeciv_agent.model_agent', '--config', str(args.model_config.resolve())]
                       if args.command == 'play' else json.loads(args.agent_command_file.read_text(encoding='utf-8')))
            service.connect(args.url, args.name, args.game_port)
            try:
                run(service, command, args.max_events, args.agent_timeout,
                    output=lambda value: print(value, flush=True))
            finally:
                if service.status()['joined'] and service.status()['running']:
                    service.save()
                service.client.disconnect()
            return
        if args.command == 'host':
            from . import hosting
            if args.host_command == 'start':
                result = hosting.start(args.source, args.container, args.url, args.restore, args.name)
            else:
                result = hosting.stop(args.container, args.backup_dir)
        elif args.command == 'connect':
            result = api_call(args.runtime_dir, 'freeciv_connect', {'url': args.url, 'name': args.name, 'game_port': args.game_port})
        elif args.command == 'call':
            result = api_call(args.runtime_dir, args.name, json.loads(args.arguments))
        elif args.command == 'chat':
            result = api_call(args.runtime_dir, 'freeciv_chat', {'message': args.message})
        elif args.command == 'end-turn':
            result = api_call(args.runtime_dir, 'freeciv_end_turn', {'expected_turn': args.turn})
        elif args.command == 'shutdown':
            result = api_call(args.runtime_dir, '', path='/shutdown')
        elif args.command == 'events':
            cursor = args.after
            while True:
                options = {'after': cursor, 'timeout': 55 if args.follow else 0}
                if args.kind:
                    options['kinds'] = args.kind
                result = api_call(args.runtime_dir, 'freeciv_events', options)
                if result['truncated']:
                    print('Warning: older events were evicted; resync with status.', file=sys.stderr)
                for event in result['events']:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                cursor = result['cursor']
                if not args.follow and not result['has_more']:
                    break
            return
        else:
            result = api_call(args.runtime_dir, 'freeciv_' + args.command)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except KeyboardInterrupt:
        return
    except Exception as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)
