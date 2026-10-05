"""Initialize platform credentials or set its webhook; plaintext stays in memory."""

import argparse
import getpass
import secrets
from urllib.parse import urlsplit

from manage import RUNTIME, decrypt, execute, sops_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "webhook"])
    args = parser.parse_args()
    values = decrypt(RUNTIME)
    if args.action == "init":
        values.setdefault("NOEDAERI_PLATFORM_API_KEY", secrets.token_urlsafe(48))
        values.setdefault("NOEDAERI_PLATFORM_WEBHOOK_SECRET", secrets.token_urlsafe(48))
        values.setdefault("NOEDAERI_PLATFORM_WEBHOOK_URL", "")
        values.setdefault("PLATFORM_RESULT_TTL_SECONDS", "604800")
    else:
        address = getpass.getpass("플랫폼 HTTPS 웹훅 주소 (입력 숨김): ").strip()
        parsed = urlsplit(address)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in address)
        ):
            raise SystemExit("Invalid webhook URL; unchanged")
        if not values.get("NOEDAERI_PLATFORM_API_KEY") or not values.get(
            "NOEDAERI_PLATFORM_WEBHOOK_SECRET"
        ):
            raise SystemExit("Initialize credentials first")
        values["NOEDAERI_PLATFORM_WEBHOOK_URL"] = address

    # dotenv quoted values preserve existing strings without interpolation.
    def quoted(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    plaintext = (
        "\n".join(f"{key}={quoted(value)}" for key, value in values.items()).encode() + b"\n"
    )
    encrypted = execute(
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
    ).stdout
    from io import StringIO

    from dotenv import dotenv_values

    checked = execute(
        ["sops", "decrypt", "--input-type", "dotenv", "--output-type", "dotenv"],
        input=encrypted,
        env=sops_env(),
    ).stdout
    if dotenv_values(stream=StringIO(checked.decode()), interpolate=False) != values:
        raise SystemExit("Encrypted roundtrip failed; unchanged")
    pending = RUNTIME.with_suffix(".pending")
    try:
        with pending.open("xb") as file:
            file.write(encrypted)
        pending.replace(RUNTIME)
    finally:
        pending.unlink(missing_ok=True)
    print("Encrypted platform configuration saved; no secrets printed. Restart to apply.")


if __name__ == "__main__":
    main()
