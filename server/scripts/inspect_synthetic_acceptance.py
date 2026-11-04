"""Read only the newest synthetic acceptance database, never production."""
import json
from pathlib import Path
import sqlite3
import sys

candidate = Path(sys.argv[1]).resolve()
if candidate.parent != Path('/var/tmp') or not candidate.name.startswith('signaldesk-knowledge-candidate-'):
    raise SystemExit('Invalid private candidate')
database = max((p for p in candidate.glob('live-synthetic-*.db') if not p.is_symlink()), key=lambda p: p.stat().st_mtime)
with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
    rows = db.execute("SELECT title,status,method,status_note,substr(content,1,240) FROM knowledge_item WHERE title LIKE 'Synthetic real-provider %'").fetchall()
print(json.dumps(dict(rows=rows), ensure_ascii=False))
