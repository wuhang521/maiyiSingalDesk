"""Build the reviewed desktop revision locally; never install or publish it."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'artifacts/task-page-access-travel-20261111'
OUTPUT = ROOT / 'release/task-page-access-travel-20261111'
NODE = Path(shutil.which('node') or 'node')

def main():
    assert ROOT == Path('D:/maiyi/SignalDesk') and not OUTPUT.exists()
    EVIDENCE.mkdir(exist_ok=True)
    config = json.loads((ROOT / 'artifacts/task-page-access-build-20261110/electron-builder.json').read_text('utf-8'))
    config['directories']['output'] = str(OUTPUT)
    config['win']['artifactName'] = 'SignalDesk-Setup-0.6.4-PageAccessTravel-20261111.${ext}'
    config_path = EVIDENCE / 'electron-builder.json'
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), 'utf-8')
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CSC_', 'WIN_CSC_', 'GH_TOKEN', 'GITHUB_TOKEN', 'DEBUG'))}
    environment.update({'PATH': str(NODE.parent) + os.pathsep + environment['PATH'],
        'ELECTRON_BUILDER_CACHE': 'D:/maiyi/tmp/health064-builder-cache',
        'CSC_IDENTITY_AUTO_DISCOVERY': 'false', 'npm_config_offline': 'true',
        'ELECTRON_BUILDER_BINARIES_MIRROR': 'http://127.0.0.1:9/'})
    command = [str(NODE), 'node_modules/electron-builder/cli.js', '--config', str(config_path), '--win', '--dir', '--x64', '--publish', 'never']
    with (EVIDENCE / 'builder.log').open('xb') as log:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
    record = {'exit_code': result.returncode, 'command': command, 'publish': False, 'installed': False}
    (EVIDENCE / 'builder-result.json').write_text(json.dumps(record, indent=2), 'utf-8')
    print(json.dumps(record), flush=True)
    return result.returncode

if __name__ == '__main__':
    sys.exit(main())
