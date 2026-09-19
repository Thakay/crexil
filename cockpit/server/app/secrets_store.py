"""App-managed secret store: the setup route's equivalent of a '.env'.

One tiny JSON file next to the SQLite state (dev: cockpit/server/, docker:
the cockpit-data volume), 0600, atomic writes, git-ignored. Precedence is
resolved by the caller: an env/.env value always WINS over a stored one —
this store only fills the gap when the environment does not provide the
secret. Values are never logged and never returned by any API endpoint
(the setup route shows a masked form only: the first six and last four
characters).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile

from . import config


def _path():
    return config.DB_PATH.parent / "secrets.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".secrets-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)  # atomic; perms travel with the inode
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def get_secret(name: str) -> str | None:
    v = _load().get(name)
    return v if isinstance(v, str) and v else None


def set_secret(name: str, value: str) -> None:
    data = _load()
    data[name] = value
    _write(data)


def delete_secret(name: str) -> None:
    data = _load()
    if name in data:
        del data[name]
        _write(data)
