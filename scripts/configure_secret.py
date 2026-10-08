"""Collect a secret in a masked native dialog and save only SOPS ciphertext."""

import argparse
import os
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    path = Path(args.config)
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.key):
        raise SystemExit("Invalid variable name")
    if not re.fullmatch(r"config/[a-z0-9_-]+\.enc\.env", path.as_posix()):
        raise SystemExit("Expected config/<environment>.enc.env")
    env = dict(os.environ)
    env.setdefault("SOPS_AGE_KEY_FILE", str(Path.home() / ".config/sops/age/keys.txt"))
    existing = b""
    if path.exists():
        result = subprocess.run(
            ["sops", "decrypt", "--input-type", "dotenv", "--output-type", "dotenv", str(path)],
            capture_output=True,
            env=env,
        )
        if result.returncode:
            raise SystemExit("Existing configuration could not be decrypted; unchanged")
        existing = result.stdout
    script = (
        'tell application "System Events"\n'
        "activate\n"
        f'set answer to display dialog "{args.key} 값을 입력하세요.\\n'
        '입력값은 암호화하여 저장됩니다." default answer "" with hidden answer '
        'buttons {"취소", "암호화하여 저장"} default button "암호화하여 저장" '
        'cancel button "취소" with title "뇌대리 · API 키 설정"\n'
        "return text returned of answer\nend tell"
    )
    answer = subprocess.run(["osascript", "-e", script], capture_output=True)
    if answer.returncode:
        print("Cancelled; configuration unchanged.")
        return
    secret = answer.stdout.rstrip(b"\r\n")
    if not secret or any(char < 33 or char > 126 for char in secret):
        raise SystemExit("Empty or invalid API key; configuration unchanged")
    # Restrict to header-token characters so dotenv has no interpolation or quote ambiguity.
    if not re.fullmatch(rb"[A-Za-z0-9._~+/=:-]+", secret):
        raise SystemExit("Unsupported key characters; configuration unchanged")
    prefix = args.key.encode() + b"="
    lines = [line for line in existing.splitlines() if not line.startswith(prefix)]
    plaintext = b"\n".join([*lines, prefix + secret]) + b"\n"
    encrypted = subprocess.run(
        [
            "sops",
            "encrypt",
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            "--filename-override",
            str(path),
        ],
        input=plaintext,
        capture_output=True,
        env=env,
    )
    if encrypted.returncode or secret in encrypted.stdout:
        raise SystemExit("Encryption verification failed; configuration unchanged")
    checked = subprocess.run(
        ["sops", "decrypt", "--input-type", "dotenv", "--output-type", "dotenv"],
        input=encrypted.stdout,
        capture_output=True,
        env=env,
    )
    if checked.returncode or checked.stdout != plaintext:
        raise SystemExit("Decryption verification failed; configuration unchanged")
    path.parent.mkdir(exist_ok=True)
    pending = path.with_suffix(".pending")
    try:
        with pending.open("xb") as target:
            target.write(encrypted.stdout)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)
    print("Secret saved encrypted; decryption roundtrip verified. No secret printed.")


if __name__ == "__main__":
    main()
