"""Decrypt SOPS dotenv files in memory and launch an approved native component."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["api", "worker", "migrate"])
    parser.add_argument("--config", action="append", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.setdefault("SOPS_AGE_KEY_FILE", str(Path.home() / ".config/sops/age/keys.txt"))
    # SOPS owns dotenv serialization. python-dotenv parses it without interpolation.
    sys.path.insert(0, str(root / "src"))
    from io import StringIO

    from dotenv import dotenv_values

    for config in args.config:
        result = subprocess.run(
            ["sops", "decrypt", "--input-type", "dotenv", "--output-type", "dotenv", config],
            capture_output=True,
            env=env,
        )
        if result.returncode:
            raise SystemExit("SOPS decryption failed; no component started")
        values = dotenv_values(stream=StringIO(result.stdout.decode()), interpolate=False)
        if any(value is None for value in values.values()):
            raise SystemExit("Invalid environment configuration")
        env.update(values)
    env["PYTHONPATH"] = str(root / "src")
    commands = {
        "api": [
            sys.executable,
            "-m",
            "uvicorn",
            "noedaeri.api:create_app",
            "--factory",
            "--host",
            env.get("API_BIND", "127.0.0.1"),
            "--port",
            env.get("API_PORT", "8000"),
            "--no-access-log",
            "--no-proxy-headers",
        ],
        "worker": [sys.executable, "-m", "noedaeri.worker"],
        "migrate": [
            sys.executable,
            "-c",
            "from noedaeri.config import Settings; from noedaeri.db import Database; "
            "Database(Settings.from_env().database_url).migrate()",
        ],
    }
    os.chdir(root)
    os.execvpe(commands[args.component][0], commands[args.component], env)


if __name__ == "__main__":
    main()
