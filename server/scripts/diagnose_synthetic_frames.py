"""Diagnose local conversion of generated test video only; never call a provider."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
if root.parent != Path('/var/tmp') or not root.name.startswith('signaldesk-knowledge-candidate-'):
    raise SystemExit('Invalid private candidate')
video = max(root.glob('synthetic-video-*.mp4'), key=lambda p: p.stat().st_mtime)
probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(video)], capture_output=True, text=True, timeout=15)
metadata = json.loads(probe.stdout)
report = {'metadata': metadata, 'commands': []}
with tempfile.TemporaryDirectory(prefix='synthetic-frames-', dir=root) as temporary:
    for index, at in enumerate([0.0, max(0, float(metadata['format']['duration']) - .1)]):
        output = Path(temporary) / ('frame-' + str(index) + '.png')
        command = ['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-ss', str(at), '-i', str(video), '-map', '0:v:0', '-an', '-sn', '-dn', '-frames:v', '1', '-vf', "scale=w='min(1600,iw)':h='min(1600,ih)':force_original_aspect_ratio=decrease", '-n', str(output)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        report['commands'].append({'at': at, 'code': result.returncode, 'stderr': result.stderr[-2000:], 'png_bytes': output.stat().st_size if output.exists() else None})
print(json.dumps(report, ensure_ascii=False, indent=2))
