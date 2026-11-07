"""Optional loopback transport for command-line clients; MCP doesn't need it."""
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .tools import validate


def make_server(service, runtime_dir, port=8788):
    runtime_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != '/health':
                return self.reply(404, {'error': 'Not found'})
            return self.reply(200, {'ok': True, 'service': 'freeciv-agent-harness'})

        def do_POST(self):
            # This API is for native clients, never websites. Bind loopback only
            # and reject browser origins to prevent CSRF into game controls.
            if self.headers.get('Origin') is not None:
                return self.reply(403, {'error': 'Browser requests are not supported'})
            expected = 'Bearer ' + token
            if not hmac.compare_digest(self.headers.get('Authorization', ''), expected):
                return self.reply(401, {'error': 'Unauthorized'})
            if self.path == '/shutdown':
                self.reply(200, {'stopping': True})
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            if self.path != '/call':
                return self.reply(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    raise ValueError('Invalid request size')
                request = json.loads(self.rfile.read(length))
                name, args = request['name'], request.get('arguments', {})
                validate(name, args)
                self.reply(200, {'result': service.call(name, args)})
            except (ValueError, KeyError, TypeError) as error:
                self.reply(400, {'error': str(error)})
            except Exception as error:
                self.reply(409, {'error': str(error)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    runtime = {'url': f'http://127.0.0.1:{server.server_port}', 'token': token}
    path = runtime_dir / 'connection.json'
    path.write_text(json.dumps(runtime), encoding='utf-8')
    path.chmod(0o600)
    return server, runtime
