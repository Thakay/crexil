"""The 3D runner's own record of a take, and the funnel it imports.

A take's recorded time includes the engine load it waited for, and the record
says how long that load took, so a first take never reads shorter than it ran
(law 17). The funnel reads the checkout's `.env` only when a run resolves a
promoted asset or promotes, never when the runner imports it. The take runs a
stub funnel and a stand-in resident engine: no GPU, no engine environment.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from test_runner_guards import REPO, threed

STUB_FUNNEL = '''
import json, sys
from pathlib import Path
argv = sys.argv
out, record = Path(argv[argv.index("--out-dir") + 1]), Path(argv[argv.index("--json") + 1])
glb = out / "trellis.glb"
glb.write_bytes(b"glTF")
record.write_text(json.dumps({"engines": {"trellis2": {"glb": str(glb), "wall_s": 0.01}}}))
'''


class SlowResident:
    """A resident engine that takes `load_s` to load for its first take."""

    def __init__(self, sock: Path, load_s: float):
        self.sock, self.load_s, self.loaded = sock, load_s, False

    def ensure(self, kind, engine):
        if not self.loaded:
            time.sleep(self.load_s)
            self.loaded = True
        return self.sock

    def release(self, kind, engine):
        return None

    def evict(self, reason):
        pass


def test_a_takes_recorded_time_includes_the_engine_load_it_waited_for(tmp_path, monkeypatch):
    stub = tmp_path / 'funnel.py'
    stub.write_text(STUB_FUNNEL)
    for name, value in (('RUNS', tmp_path / 'runs'), ('JOBS', tmp_path / 'runs/.jobs'), ('PYTHON', Path(sys.executable)),
                        ('FUNNEL', stub), ('RESIDENT', SlowResident(tmp_path / 'engine.sock', 0.4))):
        monkeypatch.setattr(threed, name, value, raising=False)
    worker = object.__new__(threed.Worker)              # the worker without its thread
    worker.lock, worker.running, worker.proc, worker.warm_key, worker.cancelled = \
        threed.threading.Lock(), None, None, None, set()
    took = []
    for job_id in ('first', 'second'):
        (tmp_path / 'runs' / job_id).mkdir(parents=True)
        threed.write_record({'id': job_id, 'kind': 'generate', 'engine': 'trellis2', 'state': 'queued', 'seed': 7})
        worker._run(job_id)
        took.append(threed.read_record(job_id))
    first, second = took
    assert first['state'] == second['state'] == 'done'
    assert first['resident']['load_s'] >= 0.4 and first['elapsed_s'] >= first['resident']['load_s']
    assert second['resident']['load_s'] < 0.4                   # the engine was already loaded
    assert second['elapsed_s'] < first['elapsed_s']


def test_importing_the_funnel_never_opens_the_checkouts_env(tmp_path):
    checkout = tmp_path / 'checkout'
    (checkout / 'tools/3d').mkdir(parents=True)
    shutil.copy(REPO / 'tools/3d/funnel.py', checkout / 'tools/3d/funnel.py')
    promoted = tmp_path / 'promoted'
    (checkout / '.env').write_text(f'PROMOTE_DIR={promoted}\n')
    probe = (
        "import json, sys\n"
        "opened = []\n"
        "sys.addaudithook(lambda event, args: opened.append(str(args[0])) if event == 'open' else None)\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import funnel\n"
        "on_import = [p for p in opened if p.endswith('.env')]\n"
        "print(json.dumps({'on_import': on_import, 'promote_dir': str(funnel.promote_dir())}))\n")
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(tmp_path)}                # no PROMOTE_DIR of its own
    answer = subprocess.run([sys.executable, '-I', '-c', probe, str(checkout / 'tools/3d')], capture_output=True,
                            text=True, env=env, timeout=60, check=True)
    found = json.loads(answer.stdout)
    assert found == {'on_import': [], 'promote_dir': str(promoted)}     # read when asked, never on import
