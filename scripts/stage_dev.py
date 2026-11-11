"""Stage the development package, never workspace data or credentials."""
from pathlib import Path
import hashlib
import os
import shutil

ROOT=Path(__file__).resolve().parents[1]
HOME=Path(os.environ['LOCALAPPDATA'])/'hermes'
DEST=HOME/'plugins/mapdesk'
FILES=['plugin.yaml','__init__.py','mapdesk_tools.py','mapdesk_runtime.py',
       'mapdesk_core.py','mapdesk_sources.py','mapdesk_formats.py','mapdesk_ops.py','mapdesk_exports.py',
       'README.md','LICENSE','after-install.md',
       'dashboard/manifest.json','dashboard/plugin_api.py','desktop/plugin.js',
       'desktop/map.html','desktop/map_keyless.html']

def stage():
    for name in FILES:
        source,target=ROOT/name,DEST/name
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        assert hashlib.sha256(source.read_bytes()).digest()==hashlib.sha256(target.read_bytes()).digest()
    print(f'Staged and hash-verified {len(FILES)} package files at {DEST}')
    print('No workspace database, credentials or configuration files changed.')

if __name__=='__main__':stage()
