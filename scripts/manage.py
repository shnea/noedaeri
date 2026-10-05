"""Native user-session services. Configuration is decrypted only in memory."""

import argparse
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "config/runtime.enc.env"
COMPONENTS = ("database", "api", "worker", "proxy")


def execute(command, *, env=None, input=None, required=True):
    result = subprocess.run(command, env=env, input=input, capture_output=True)
    if required and result.returncode:
        # Subprocess diagnostics may contain decrypted addresses and identifiers.
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode})")
    return result


def sops_env():
    env = dict(os.environ)
    env.setdefault("SOPS_AGE_KEY_FILE", str(Path.home() / ".config/sops/age/keys.txt"))
    return env


def decrypt(path):
    result = execute(
        ["sops", "decrypt", "--input-type", "dotenv", "--output-type", "dotenv", str(path)],
        env=sops_env(),
    )
    values = dotenv_values(stream=StringIO(result.stdout.decode()), interpolate=False)
    if any(value is None for value in values.values()):
        raise RuntimeError("Invalid configuration")
    return values


def encrypt(values):
    if RUNTIME.exists():
        raise RuntimeError("Runtime configuration already exists; refusing to replace it")
    # Quote dotenv values; spaces in native application-data paths are valid.
    plaintext = "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items()).encode()
    result = execute(
        [
            "sops",
            "encrypt",
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            "--filename-override",
            "config/runtime.enc.env",
        ],
        input=plaintext,
        env=sops_env(),
    )
    checked = execute(
        ["sops", "decrypt", "--input-type", "dotenv", "--output-type", "dotenv"],
        input=result.stdout,
        env=sops_env(),
    )
    if checked.stdout != plaintext:
        raise RuntimeError("Encrypted configuration verification failed")
    for value in values.values():
        if len(value) > 8 and value.encode() in result.stdout:
            raise RuntimeError("Plaintext detected in encrypted configuration")
    with RUNTIME.open("xb") as output:
        output.write(result.stdout)


def label(component):
    return "io.noedaeri." + component


def domain():
    return f"gui/{os.getuid()}"


def plist_path(component):
    return Path.home() / "Library/LaunchAgents" / (label(component) + ".plist")


def loaded(component):
    return (
        execute(
            ["launchctl", "print", domain() + "/" + label(component)], required=False
        ).returncode
        == 0
    )


def write_agents(values):
    for component in COMPONENTS:
        path = plist_path(component)
        path.parent.mkdir(parents=True, exist_ok=True)
        arguments = [sys.executable, str(ROOT / "scripts/manage.py"), "serve", component]
        payload = {
            "Label": label(component),
            "ProgramArguments": arguments,
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": {"PATH": values["EXEC_PATH"]},
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "ExitTimeOut": 20,
            "ProcessType": "Background",
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
            "Umask": 63,
        }
        if path.exists():
            previous = plistlib.loads(path.read_bytes())
            if previous.get("ProgramArguments") != arguments:
                raise RuntimeError("A different installation owns this launch agent")
        path.write_bytes(plistlib.dumps(payload))
        path.chmod(0o600)


def start_component(component):
    if not loaded(component):
        execute(["launchctl", "enable", domain() + "/" + label(component)])
        execute(["launchctl", "bootstrap", domain(), str(plist_path(component))])


def stop_component(component):
    result = execute(["launchctl", "print", domain() + "/" + label(component)], required=False)
    if result.returncode:
        return
    match = re.search(r"^\s*pid = (\d+)\s*$", result.stdout.decode(), re.MULTILINE)
    execute(["launchctl", "bootout", domain() + "/" + label(component)])
    if match:
        for _ in range(60):
            try:
                os.kill(int(match[1]), 0)
            except ProcessLookupError:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Service process has not exited")
    # launchd may finish deregistration after bootout returns.
    time.sleep(0.5)


def wait_database(values):
    for _ in range(30):
        try:
            with psycopg.connect(values["DATABASE_URL"], connect_timeout=1) as connection:
                connection.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            time.sleep(0.5)
    raise RuntimeError("Database readiness check failed")


def initialize(state_dir, port):
    if RUNTIME.exists():
        raise RuntimeError("Already configured; use start or status")
    state = Path(state_dir).expanduser().resolve()
    if state == ROOT or state.is_relative_to(ROOT / "tmp"):
        raise RuntimeError("Persistent state must be outside temporary storage")
    if state.exists() and any(state.iterdir()):
        raise RuntimeError("State directory is not empty; refusing to overwrite")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    socket_dir = state / "socket"
    socket_dir.mkdir(mode=0o700)
    pg_bin = Path(execute(["brew", "--prefix", "postgresql@17"]).stdout.decode().strip()) / "bin"
    nginx = shutil.which("nginx")
    if not nginx:
        raise RuntimeError("nginx is not installed")
    admin, app_user, db_name = ("nd_" + secrets.token_hex(6) for _ in range(3))
    execute(
        [
            str(pg_bin / "initdb"),
            "-D",
            str(state / "pgdata"),
            "-U",
            admin,
            "--auth-local=trust",
            "--auth-host=reject",
            "--no-locale",
            "-E",
            "UTF8",
        ]
    )
    # The Unix socket lives in an owner-only directory. PostgreSQL has no TCP listener.
    config = state / "pgdata/postgresql.conf"
    with config.open("a") as file:
        escaped_socket = str(socket_dir).replace("'", "''")
        file.write(f"\nlisten_addresses = ''\nunix_socket_directories = '{escaped_socket}'\n")
        file.write("unix_socket_permissions = 0700\nlogging_collector = off\n")
    ctl = [str(pg_bin / "pg_ctl"), "-D", str(state / "pgdata")]
    execute([*ctl, "-l", "/dev/null", "-w", "start"])
    try:
        with psycopg.connect(
            make_conninfo(host=str(socket_dir), user=admin, dbname="postgres"), autocommit=True
        ) as conn:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(app_user)
                )
            )
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_name), sql.Identifier(app_user)
                )
            )
    finally:
        execute([*ctl, "-m", "fast", "-w", "stop"])
    platform = decrypt(ROOT / "config/platform.enc.env")
    callback = urlsplit(platform["PLATFORM_OIDC_REDIRECT_URI"])
    # Select a free loopback API port; nginx alone handles the ingress port.
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        api_port = probe.getsockname()[1]
    values = {
        "DATABASE_URL": make_conninfo(host=str(socket_dir), user=app_user, dbname=db_name),
        "WORKER_API_KEY": secrets.token_urlsafe(48),
        "WORKER_API_ORIGIN": f"http://127.0.0.1:{api_port}",
        "PUBLIC_ORIGIN": f"{callback.scheme}://{callback.netloc}",
        "STORAGE_ROOT": str(ROOT / "tmp"),
        "STATE_ROOT": str(state),
        "API_BIND": "127.0.0.1",
        "API_PORT": str(api_port),
        "INGRESS_PORT": str(port),
        "PG_BIN": str(pg_bin),
        "NGINX_BIN": nginx,
        "EXEC_PATH": os.environ["PATH"],
    }
    encrypt(values)
    write_agents(values)
    print("Initialized private database and encrypted runtime configuration.")


def nginx_config(values):
    state = Path(values["STATE_ROOT"])

    def quote(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    text = f"""
worker_processes 1;
pid {quote(state / "nginx.pid")};
error_log /dev/null;
events {{ worker_connections 256; }}
http {{
  access_log off;
  server_tokens off;
  client_body_temp_path {quote(state / "nginx-body")};
  proxy_temp_path {quote(state / "nginx-proxy")};
  server {{
    listen {int(values["INGRESS_PORT"])};
    client_max_body_size {int(values.get("UPLOAD_MAX_BYTES", 5 * 1024**3))};
    client_body_timeout 30s;
    location ^~ /internal/ {{ return 404; }}
    location = /openapi.json {{ return 404; }}
    location / {{
      proxy_pass {values["WORKER_API_ORIGIN"]};
      proxy_http_version 1.1;
      proxy_set_header Host $http_host;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_request_buffering off;
      proxy_buffering off;
      proxy_read_timeout 3600s;
      proxy_send_timeout 60s;
    }}
  }}
}}
"""
    path = state / "nginx.conf"
    path.write_text(text)
    path.chmod(0o600)
    return path


def serve(component):
    values = decrypt(RUNTIME)
    # launchd does not inherit the interactive shell locale. PostgreSQL on macOS
    # requires an explicit locale before starting its child processes.
    env = dict(os.environ, PATH=values["EXEC_PATH"], LC_ALL="C", LANG="C")
    if component == "database":
        args = [
            str(Path(values["PG_BIN"]) / "postgres"),
            "-D",
            str(Path(values["STATE_ROOT"]) / "pgdata"),
        ]
    elif component == "proxy":
        path = nginx_config(values)
        args = [values["NGINX_BIN"], "-c", str(path), "-g", "daemon off;"]
        execute([values["NGINX_BIN"], "-t", "-c", str(path)], env=env)
    else:
        args = [
            sys.executable,
            str(ROOT / "scripts/run.py"),
            component,
            "--config",
            "config/platform.enc.env",
            "--config",
            "config/runtime.enc.env",
        ]
        if component == "api" and (ROOT / "config/ai-providers.enc.env").exists():
            # Provider keys stay out of the worker process.
            args += ["--config", "config/ai-providers.enc.env"]
    os.chdir(ROOT)
    os.execvpe(args[0], args, env)


def status(values):
    for component in COMPONENTS:
        result = execute(["launchctl", "print", domain() + "/" + label(component)], required=False)
        text = result.stdout.decode()
        running = result.returncode == 0 and "state = running" in text
        print(f"{component}: {'running' if running else 'stopped or restarting'}")
    for name, origin in [
        ("API", values["WORKER_API_ORIGIN"]),
        ("Ingress", "http://127.0.0.1:" + values["INGRESS_PORT"]),
    ]:
        try:
            response = httpx.get(origin + "/health/live", timeout=5)
            print(f"{name} health: HTTP {response.status_code}")
        except httpx.HTTPError:
            print(f"{name} health: unavailable")
    try:
        with psycopg.connect(values["DATABASE_URL"], connect_timeout=2) as conn:
            row = conn.execute(
                "SELECT count(*) FROM workers WHERE last_seen>now()-interval '30 seconds'"
            ).fetchone()
            print(f"Recent worker heartbeats: {row[0]}")
    except psycopg.Error:
        print("Database query: unavailable")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "start", "stop", "restart", "status", "serve"])
    parser.add_argument("component", nargs="?", choices=COMPONENTS)
    parser.add_argument("--state-dir")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.action == "init":
        if not args.state_dir or not args.port or not 1024 <= args.port <= 65535:
            parser.error("init requires --state-dir and --port (1024..65535)")
        initialize(args.state_dir, args.port)
        return
    if args.action == "serve":
        if not args.component:
            parser.error("serve requires a component")
        serve(args.component)
        return
    values = decrypt(RUNTIME)
    if args.action == "status":
        status(values)
        return
    if args.action in {"stop", "restart"}:
        for component in reversed(COMPONENTS):
            stop_component(component)
        if args.action == "stop":
            print("Services stopped. Persistent data and encrypted settings retained.")
            return
    write_agents(values)
    start_component("database")
    wait_database(values)
    execute(
        [
            sys.executable,
            "scripts/run.py",
            "migrate",
            "--config",
            "config/platform.enc.env",
            "--config",
            "config/runtime.enc.env",
        ]
    )
    for component in ("api", "worker", "proxy"):
        start_component(component)
    print("Services loaded into the current login session; verify with status.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, psycopg.Error):
        raise SystemExit(
            "Native service operation failed; credentials and diagnostics were withheld."
        ) from None
