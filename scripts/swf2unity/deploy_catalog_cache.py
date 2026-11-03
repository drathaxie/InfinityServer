"""Hash-guarded one-time activation of catalog revision tracking for native asset replacements."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(target, source):
    metadata = target.stat()
    fd, temporary = tempfile.mkstemp(prefix='.catalog-cache-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(source.read_bytes()); output.flush(); os.fsync(output.fileno())
        os.chmod(temporary, metadata.st_mode & 0o777); os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def main():
    stage = Path(__file__).parent
    payload = json.loads((stage / 'payload.json').read_text())
    target = Path('/opt/infinity/server/assetwire.py')
    assert sha(target) == payload['before_sha256']
    assert sha(stage / 'assetwire.py') == payload['new_sha256']
    subprocess.run(['/opt/infinity/.venv/bin/python', str(stage / 'test_assetwire_catalog.py')], check=True, capture_output=True)
    process_ids = [payload['game_pid'], payload['api_pid']]
    listing = subprocess.check_output(['ss', '-ltnp'], text=True)
    ports = sorted({int(line.split()[3].rsplit(':', 1)[-1]) for line in listing.splitlines()
        if any('pid=' + process_id + ',' in line for process_id in process_ids)})
    assert ports
    backup = Path('/opt/infinity/backups') / datetime.now(timezone.utc).strftime('native-catalog-cache-%Y%m%dT%H%M%SZ')
    backup.mkdir(mode=0o700)
    shutil.copy2(target, backup / target.name); shutil.copy2(stage / 'payload.json', backup / 'payload.json')
    try:
        install(target, stage / 'assetwire.py'); assert sha(target) == payload['new_sha256']
        subprocess.run(['systemctl', 'restart', 'infinity-api', 'infinity-game'], check=True, timeout=60)
        services = subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()
        assert services == ['active', 'active']
        for port in ports:
            for attempt in range(160):
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=1): pass
                    break
                except OSError:
                    if attempt == 159: raise
                    time.sleep(0.25)
    except Exception:
        install(target, backup / target.name)
        subprocess.run(['systemctl', 'restart', 'infinity-api', 'infinity-game'], check=True, timeout=60)
        raise
    game_pid = subprocess.check_output(['systemctl', 'show', 'infinity-game', '-p', 'MainPID', '--value'], text=True).strip()
    result = dict(status='activated', backup=str(backup), sha256=sha(target), services=services,
        listening_ports=ports, old_game_pid=payload['game_pid'], game_pid=game_pid)
    (backup / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
