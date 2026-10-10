from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _script(tmp_path, name):
    scripts = tmp_path / "scripts"
    scripts.mkdir(exist_ok=True)
    return Path(shutil.copyfile(ROOT / "scripts" / name, scripts / name))


def _client(tmp_path, code):
    client = tmp_path / "postgres_client"
    client.write_text("#!/usr/bin/env python\n" + code)
    client.chmod(0o700)
    return client


def test_backup_keeps_credentials_out_of_argv_and_files_private(tmp_path):
    script = _script(tmp_path, "backup_postgres.sh")
    client = _client(tmp_path, '''import json, os, pathlib, sys
pathlib.Path(os.environ["CAPTURE"]).write_text(json.dumps({"argv": sys.argv[1:], "database": os.environ["PGDATABASE"]}))
for argument in sys.argv[1:]:
    if argument.startswith("--file="):
        pathlib.Path(argument.split("=", 1)[1]).write_text("archive")
''')
    capture = tmp_path / "capture.json"
    uri = "postgresql+psycopg://test:fake-password@127.0.0.1:1/test"
    env = os.environ | {"ADVISTA_DATABASE_URL": uri, "PG_DUMP_BIN": str(client), "CAPTURE": str(capture)}
    results = [subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False) for _ in range(2)]
    assert all(result.returncode == 0 for result in results)
    backups = [Path(result.stdout.strip()) for result in results]
    assert backups[0] != backups[1]
    for backup in backups:
        assert backup.read_text() == "archive"
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    command = json.loads(capture.read_text())
    assert command["database"] == uri.replace("postgresql+psycopg", "postgresql")
    assert "fake-password" not in " ".join(command["argv"])


def test_backup_failure_does_not_leave_an_archive(tmp_path):
    script = _script(tmp_path, "backup_postgres.sh")
    client = _client(tmp_path, "import sys\nsys.exit(1)\n")
    env = os.environ | {"ADVISTA_DATABASE_URL": "postgresql://127.0.0.1:1/test", "PG_DUMP_BIN": str(client)}
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stdout == ""
    assert not list((tmp_path / "outputs" / "backups").glob("*.dump"))


def test_archive_verification_uses_configured_client(tmp_path):
    script = _script(tmp_path, "verify_postgres_backup.sh")
    client = _client(tmp_path, '''import sys
assert sys.argv[1:] == ["--list", sys.argv[2]]
''')
    backup = tmp_path / "backup.dump"
    backup.write_text("archive")
    result = subprocess.run(["bash", str(script), str(backup)], env=os.environ | {"PG_RESTORE_BIN": str(client)}, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "archive is readable" in result.stdout
