"""Run integration tests against a disposable, socket-only PostgreSQL cluster."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    pg_bin = os.environ.get("POSTGRES_BIN")
    if not pg_bin:
        located = shutil.which("initdb")
        if located:
            pg_bin = str(Path(located).parent)
        else:
            prefix = subprocess.check_output(["brew", "--prefix", "postgresql@17"], text=True)
            pg_bin = str(Path(prefix.strip()) / "bin")
    with tempfile.TemporaryDirectory(prefix="noedaeri-test-") as name:
        root = Path(name)
        data, sock = root / "db", root / "socket"
        sock.mkdir(mode=0o700)
        subprocess.run(
            [
                str(Path(pg_bin) / "initdb"),
                "-D",
                str(data),
                "-U",
                "test",
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        ctl = str(Path(pg_bin) / "pg_ctl")
        subprocess.run(
            [
                ctl,
                "-D",
                str(data),
                "-l",
                str(root / "postgres.log"),
                "-o",
                f"-k {sock} -c listen_addresses=''",
                "-w",
                "start",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        try:
            env = dict(os.environ, TEST_DATABASE_URL=f"host={sock} dbname=postgres user=test")
            return subprocess.call([sys.executable, "-m", "pytest", "-q"], env=env)
        finally:
            subprocess.run(
                [ctl, "-D", str(data), "-m", "immediate", "-w", "stop"],
                check=True,
                stdout=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    raise SystemExit(main())
