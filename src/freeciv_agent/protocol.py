import json
from importlib.resources import files


class Protocol:
    """Versioned wire facts, generated from an upstream packets.def file."""

    def __init__(self, path=None):
        text = (files('freeciv_agent') / 'profiles' / 'web-3.3.json').read_text() if path is None else path.read_text()
        self.profile = json.loads(text)
        self.packets = self.profile['packets']
        self.names = {p['id']: name for name, p in self.packets.items()}

    def name(self, pid):
        return self.names.get(pid, 'unknown_' + str(pid))

    def packet(self, packet_name, **fields):
        spec = self.packets[packet_name]
        if not spec['client_to_server']:
            raise ValueError('Not a client packet: ' + packet_name)
        return {'pid': spec['id'], **fields}


def bitset_has(value, index):
    """Freeciv-web transmits bit vectors as little-endian arrays of bytes."""
    raw = value.get('raw', []) if isinstance(value, dict) else (value or [])
    return index >= 0 and index // 8 < len(raw) and bool(raw[index // 8] & (1 << (index % 8)))
