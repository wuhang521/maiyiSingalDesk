"""Read-only diagnosis of the fixed reviewed activation probes; no raw output."""
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess

helper = Path('/var/tmp/signaldesk-health-activate-d29d5b28.py')
assert not helper.is_symlink()
assert hashlib.sha256(helper.read_bytes()).hexdigest() == 'd29d5b28afe61414e48ecab8eab7b772ebcc119e185207020ae4a0ec4ab9cbd2'
tool = runpy.run_path(str(helper))
for name, runtime, cwd, probe in (
    ('task_configuration', '/opt/signaldesk-task/.venv/bin/python', '/opt/signaldesk-task', tool['TASK_PROBE']),
    ('producer_configuration', '/opt/signaldesk/.venv/bin/python', '/opt/signaldesk', tool['COLLECTOR_PROBE']),
):
    result = subprocess.run([runtime, '-B', '-c', probe], cwd=cwd, env=tool['ENV'], capture_output=True, text=True, timeout=90)
    lines = re.findall(r'File "<string>", line (\d+)', result.stderr)
    classes = re.findall(r'^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception))(?::|$)', result.stderr, re.M)
    print(json.dumps({'stage': name, 'returncode': result.returncode, 'probe_line': int(lines[-1]) if lines else None,
                      'exception_class': classes[-1] if classes else None, 'raw_outputs_exported': False,
                      'article_or_state_writes': 0, 'messages_sent': 0}))
