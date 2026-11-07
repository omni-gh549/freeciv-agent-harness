"""Manage an explicitly selected local Freeciv-web Docker installation."""
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def run(*args, timeout=60, input=None):
    result = subprocess.run(list(args), text=True, input=input, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Command failed')
    return result.stdout.strip()


def start(source, container='freeciv-web', url='http://localhost:8080', restore=None, name='Agent'):
    source = Path(source).resolve()
    if not (source / 'docker-compose.yml').is_file():
        raise ValueError('Source must be a Freeciv-web checkout with docker-compose.yml')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', container):
        raise ValueError('Invalid container name')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name):
        raise ValueError('For save import, use a simple player name')
    exists = subprocess.run(['docker', 'inspect', container], capture_output=True).returncode == 0
    if exists:
        run('docker', 'start', container)
    else:
        if container != 'freeciv-web':
            raise ValueError('For a custom container, create it explicitly before calling host start')
        run('docker', 'compose', '-f', str(source / 'docker-compose.yml'), 'up', '-d', timeout=600)
    deadline = time.monotonic() + 120
    while True:
        try:
            with urlopen(url, timeout=3) as response:
                if response.status == 200:
                    break
        except (URLError, TimeoutError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError('Container started, but HTTP did not become ready; inspect docker logs ' + container)
        time.sleep(1)
    result = {'container': container, 'url': url, 'running': True, 'game_started': False}
    if restore:
        save = Path(restore).resolve()
        suffix = next((s for s in ('.sav.xz', '.sav.zst', '.sav.gz', '.sav.bz2', '.sav') if save.name.endswith(s)), None)
        if not save.is_file() or suffix is None:
            raise ValueError('Restore file must be an existing .sav or compressed .sav file')
        target = '/var/lib/tomcat10/webapps/data/savegames/' + name
        run('docker', 'exec', '-u', 'root', container, 'mkdir', '-p', target)
        run('docker', 'cp', str(save), container + ':' + target + '/agent_restore' + suffix)
        run('docker', 'exec', '-u', 'root', container, 'chown', '-R', 'docker:docker', target)
        result.update(save_imported=True, load_basename='agent_restore', player_name=name,
                      next='Connect with that player name and call freeciv_load(filename="agent_restore"). Ready only when humans are ready.')
    return result


def stop(container='freeciv-web', backup_dir=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', container):
        raise ValueError('Invalid container name')
    # Stop before copying to get a stable view of completed save files. Call
    # freeciv_save first; stopping alone cannot manufacture an unsaved turn.
    run('docker', 'stop', '--timeout', '20', container, timeout=45)
    result = {'container': container, 'stopped': True, 'note': 'Only completed save files are backed up; save through the connected client before stopping.'}
    if backup_dir:
        target = Path(backup_dir).resolve() / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target.mkdir(parents=True, exist_ok=False)
        run('docker', 'cp', container + ':/var/lib/tomcat10/webapps/data/savegames/.', str(target))
        result['backup_dir'] = str(target)
    return result
