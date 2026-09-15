"""Consistent SQLite backup, including WAL data, retained for seven days."""
import datetime
from pathlib import Path
import sqlite3

directory = Path('/var/lib/green-atlas/backups')
directory.mkdir(mode=0o750, exist_ok=True)
now = datetime.datetime.now(datetime.timezone.utc)
target = directory / (now.strftime('%Y%m%dT%H%M%SZ') + '.sqlite3')
with sqlite3.connect('file:/var/lib/green-atlas/green-atlas.sqlite3?mode=ro', uri=True) as source:
    with sqlite3.connect(target) as destination:
        source.backup(destination)
        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
target.chmod(0o640)
for old in directory.glob('*.sqlite3'):
    if old.is_file() and old.stat().st_mtime < now.timestamp() - 7 * 86400:
        old.unlink()
print(target.name)
