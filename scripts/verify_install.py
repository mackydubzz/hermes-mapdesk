"""Opt-in real CLI install/reinstall in a disposable Hermes home.

Uses a local Git fixture, never a public repo, live gateway or another profile.
The disposable home lives under ~/.mdt (override: MAPDESK_INSTALL_TEST_ROOT) so
Windows path limits match a real installation instead of a deeply nested one.
Dependencies may be downloaded into the disposable home's managed environment.
Run with the current Hermes Python: python scripts/verify_install.py --run
"""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Allow an isolated dependency install')
    if not parser.parse_args().run:
        parser.error('Pass --run to execute the isolated install')
    active = Path(os.environ['HERMES_HOME']).resolve()
    scratch = active / 'cache' / 'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    # Hermes copies its managed runtime below the test home, and Windows refuses
    # targets past MAX_PATH. A home nested in the profile scratch directory is
    # always longer than the real one, so use a short root and refuse to run when
    # the disposable home would still be longer than the home that already works.
    base = Path(os.environ.get('MAPDESK_INSTALL_TEST_ROOT') or Path.home() / '.mdt').resolve()
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='i-', dir=base))
    home, repo = work / 'h', work / 'source'
    if os.name == 'nt' and len(str(home)) > len(str(active)):
        work.rmdir()
        parser.error(f'Test home {home} is longer than the active Hermes home; '
                     'set MAPDESK_INSTALL_TEST_ROOT to a shorter directory')
    home.mkdir(); repo.mkdir()
    # Git on this Windows host interprets file:///C:/ as the invalid /C:/ path.
    source_url = 'file://' + repo.as_posix() if os.name == 'nt' else repo.as_uri()
    env = dict(os.environ, HERMES_HOME=str(home), HERMES_PROFILE='default',
               # A home nested under the real default root otherwise borrows its
               # dependency store. Scope the platform default in this CHILD only.
               LOCALAPPDATA=str(work / 'os-home'),
               TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch),
               GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='1',
               GIT_CONFIG_KEY_0='core.autocrlf', GIT_CONFIG_VALUE_0='false')
    env.pop('MAPDESK_MAPBOX_ACCESS_TOKEN', None)
    manifest_ast = ast.parse((ROOT / 'scripts/stage_dev.py').read_text())
    files = next(ast.literal_eval(node.value) for node in manifest_ast.body
                 if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FILES' for t in node.targets))
    for relative in files:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    # Include development manifests to prove they are not detected as a runtime sidecar.
    for relative in ('dev/package.json', 'dev/package-lock.json'):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    checks = {'scope': 'Local Git CLI install in disposable HERMES_HOME, not a marketplace GUI or clean-OS test',
              'sandbox': str(work), 'runtime_files': len(files)}
    result_file = ROOT / 'artifacts/install-acceptance.json'

    def run(argv, label, cwd=repo, timeout=240):
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout)
        output = proc.stdout + proc.stderr
        (work / (label + '.log')).write_text(output, encoding='utf-8')
        checks[label + '_exit_code'] = proc.returncode
        if proc.returncode:
            raise RuntimeError(f'{label} failed ({proc.returncode}); see {work / (label + ".log")}')
        return output

    def enabled():
        import yaml
        config = home / 'config.yaml'
        data = yaml.safe_load(config.read_text(encoding='utf-8')) if config.exists() else {}
        return 'mapdesk' in (data or {}).get('plugins', {}).get('enabled', [])

    cli = [sys.executable, '-m', 'hermes_cli.main', 'plugins']
    try:
        preflight = run([sys.executable, '-c',
            'from hermes_constants import get_hermes_home; from pm.environments import dependency_home_root; '
            'from pathlib import Path; import os; expected=Path(os.environ["HERMES_HOME"]).resolve(); '
            'assert get_hermes_home().resolve()==expected; assert dependency_home_root().resolve()==expected; '
            'print("Configuration and dependency state are scoped to the disposable home")'], 'scope-check')
        run(['git', 'init', '-q'], 'git-init')
        run(['git', '-c', 'user.name=MapDesk install test', '-c', 'user.email=mapdesk-test@example.invalid',
             'add', '.'], 'git-add')
        run(['git', '-c', 'user.name=MapDesk install test', '-c', 'user.email=mapdesk-test@example.invalid',
             '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'Local installation test fixture'], 'git-commit')
        checks['fixture_sha'] = run(['git', 'rev-parse', 'HEAD'], 'git-sha').strip()
        installed = home / 'plugins' / 'mapdesk'
        install_output = run(cli + ['install', source_url, '--ref', checks['fixture_sha'], '--yes-deps', '--enable'],
                             'install', timeout=420)
        checks['no_node_sidecar_prompt'] = 'declares Node dependencies' not in install_output
        assert checks['no_node_sidecar_prompt'], 'Development tooling triggered an end-user Node prompt'
        checks['installed_files_match'] = all(hashlib.sha256((repo / f).read_bytes()).digest() ==
                                              hashlib.sha256((installed / f).read_bytes()).digest() for f in files)
        assert checks['installed_files_match']
        checks['native_desktop_copy_present'] = (home / 'desktop-plugins/mapdesk/plugin.js').is_file()
        checks['node_modules_absent'] = not (installed / 'node_modules').exists()
        checks['enabled_after_install'] = enabled()
        run([sys.executable, '-c',
             'import json; from hermes_constants import get_hermes_home; '
             'from hermes_cli.desktop_plugin_halves import reconcile_desktop_halves_report; '
             'home=get_hermes_home(); print(json.dumps(reconcile_desktop_halves_report(home,home/"desktop-plugins")))'],
            'desktop-reconcile')
        checks['native_desktop_files_match_after_reconcile'] = all(
            hashlib.sha256((installed / 'desktop' / f).read_bytes()).digest() ==
            hashlib.sha256((home / 'desktop-plugins/mapdesk' / f).read_bytes()).digest()
            for f in ('plugin.js', 'map.html'))
        assert checks['native_desktop_files_match_after_reconcile']
        run(cli + ['doctor', 'mapdesk'], 'doctor')
        fixture = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'id': '001',
                   'properties': {'name': 'INSTALL TEST FIXTURE', 'fixture': True},
                   'geometry': {'type': 'Point', 'coordinates': [-98.49, 29.42]}}]}
        fixture_path = work / 'fixture.json'
        fixture_path.write_text(json.dumps(fixture), encoding='utf-8')
        run([sys.executable, '-c',
             'import json,sys; from pathlib import Path; from mapdesk_runtime import execute; '
             'layer=execute("import_geojson",{"name":"Install test fixture","geojson":json.loads(Path(sys.argv[1]).read_text())}); '
             'execute("select",{"layer_id":layer["id"],"feature_ids":["001"]}); '
             'execute("viewport",{"center":[-98.49,29.42],"zoom":12}); '
             'execute("save",{"name":"Isolated installation test"}); print("Fixture persisted")', str(fixture_path)],
            'write-fixture', cwd=installed)
        read = [sys.executable, '-c', 'import json; from mapdesk_runtime import execute; print(json.dumps(execute("state",{})))']
        before = json.loads(run(read, 'read-new-process', cwd=installed))
        assert len(before['layers']) == 1 and before['selection']['feature_ids'] == ['001']
        assert before['name'] == 'Isolated installation test'
        run(cli + ['install', source_url, '--ref', checks['fixture_sha'], '--force', '--yes-deps', '--enable'],
            'reinstall', timeout=420)
        after = json.loads(run(read, 'read-after-reinstall', cwd=installed))
        checks['reinstall_preserves_workspace'] = before == after
        assert checks['reinstall_preserves_workspace']
        checks['enabled_after_reinstall'] = enabled()
        assert checks['enabled_after_install'] and checks['enabled_after_reinstall'], 'CLI returned success but MapDesk was not enabled; inspect install/reinstall logs'
        checks['passed'] = True
    except Exception as error:
        checks['passed'] = False
        checks['error'] = str(error)
    result_file.parent.mkdir(exist_ok=True)
    result_file.write_text(json.dumps(checks, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(checks, indent=2))
    return 0 if checks['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
