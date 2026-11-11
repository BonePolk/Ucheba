"""Запуск тестов в уже работающих контейнерах, без Compose up/restart.

python tests/run_agent_tests.py browser
python tests/run_agent_tests.py straus
"""
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
service = sys.argv[1]
if service not in ('browser', 'straus', 'lucheba'):
    raise SystemExit('Выберите browser, straus или lucheba')
paths = list((root / 'common').rglob('*.py')) + list((root / 'services' / service).glob('*.py'))
paths += list((root / 'backend').rglob('*.py')) + [root / 'tests' / f'test_{service}_tools.py']
files = {}
for path in paths:
    name = str(path.relative_to(root))
    if name.startswith(f'services/{service}/'):
        name = path.name
    files[name] = path.read_text()
bootstrap = '''import json, pathlib, sys, tempfile, unittest
payload = json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix="agent-tests-") as folder:
    for name, text in payload['files'].items():
        path = pathlib.Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    sys.path[:0] = [folder, folder + '/tests', folder + '/backend']
    suite = unittest.defaultTestLoader.loadTestsFromNames(payload['tests']) if payload['tests'] else unittest.defaultTestLoader.discover(folder + '/tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
'''
result = subprocess.run(['docker', 'compose', 'exec', '-T', service, 'python', '-c', bootstrap],
                        input=json.dumps({'files': files, 'tests': sys.argv[2:]}), text=True, cwd=root)
raise SystemExit(result.returncode)
