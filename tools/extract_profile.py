"""Extract wire facts; does not copy upstream packet handlers or game code."""
import argparse
import json
import re
from pathlib import Path


def extract(text):
    packets = {}
    for match in re.finditer(r'^PACKET_(\w+)\s*=\s*(\d+);([^\n]*)\n(.*?)^end\s*$', text, re.M | re.S):
        name, pid, flags, body = match.groups()
        fields = []
        for line in body.splitlines():
            line = line.split('#', 1)[0].strip()
            if not line or ';' not in line:
                continue
            definition = line.split(';', 1)[0]
            if ' ' not in definition:
                continue
            field_type, names = definition.split(None, 1)
            for value in names.split(','):
                field = re.match(r'(\w+)', value.strip())
                if field:
                    fields.append({'name': field[1], 'type': field_type})
        packets[name.lower()] = {'id': int(pid), 'client_to_server': bool(re.search(r'\bcs\b', flags)), 'fields': fields}
    return packets


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('packets_def', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--capability', default='+Freeciv.Web.Devel-3.3')
    p.add_argument('--upstream-revision', default='unknown')
    a = p.parse_args()
    profile = {'name': 'Freeciv-web 3.3 development JSON protocol', 'capability': a.capability,
               'upstream': 'https://github.com/freeciv/freeciv-web', 'upstream_revision': a.upstream_revision,
               'packets': extract(a.packets_def.read_text(encoding='utf-8'))}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(profile, indent=2) + '\n', encoding='utf-8')
