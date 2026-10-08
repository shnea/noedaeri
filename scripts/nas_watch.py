"""User-login NAS reconnect helper; configuration and credentials stay in memory."""

import errno
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def mount_once(values, helper):
    payload = {
        "url": values["SERVICE_STORAGE_SMB_URL"],
        "username": values.get("SERVICE_STORAGE_SMB_USERNAME"),
        "password": values.get("SERVICE_STORAGE_SMB_PASSWORD"),
    }
    try:
        result = subprocess.run(
            [str(helper)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    try:
        status = json.loads(result.stdout)["status"]
    except (ValueError, KeyError, TypeError):
        return False
    # NetFS reports EEXIST for a share already mounted by this login session.
    return status in {0, errno.EEXIST} and os.path.ismount(values["SERVICE_STORAGE_MOUNT_ROOT"])


def watch(values):
    root = Path(values.get("SERVICE_STORAGE_MOUNT_ROOT", ""))
    helper = Path(values["STATE_ROOT"]) / "tools/mount-nas"
    if not values.get("SERVICE_STORAGE_SMB_URL") or not values.get("SERVICE_STORAGE_MOUNT_ROOT"):
        raise RuntimeError("NAS reconnect configuration is missing")
    if not helper.is_file():
        raise RuntimeError("NAS native mount helper must be compiled before starting")
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_attempt = 0.0
    while not stopping:
        if time.monotonic() >= next_attempt:
            if not os.path.ismount(root):
                mount_once(values, helper)
            next_attempt = time.monotonic() + 30
        time.sleep(1)
