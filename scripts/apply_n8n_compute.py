"""Patch only compute handoff in the configured workflow, preserving server edits."""

import argparse
import json
import os
from datetime import UTC, datetime

import httpx
from manage import ROOT, decrypt
from prepare_n8n_compute import EXECUTION_SETTINGS, prepare
from prepare_n8n_translation import prepare_translation


def require(response):
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"n8n rejected operation (HTTP {response.status_code})")
    return response.json()


def verify(saved, prepared):
    if saved["nodes"] != prepared["nodes"] or saved["connections"] != prepared["connections"]:
        raise RuntimeError("Saved workflow differs; readiness remains disabled")
    if any(saved["settings"].get(k) != v for k, v in EXECUTION_SETTINGS.items()):
        raise RuntimeError("Execution data policy differs; readiness remains disabled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Save and activate the prepared workflow"
    )
    parser.add_argument(
        "--translation", action="store_true", help="Add typed sentence translation branch"
    )
    args = parser.parse_args()
    path = ROOT / "config/n8n.enc.env"
    values = decrypt(path)
    if not values["N8N_ORIGIN"].startswith("https://"):
        raise RuntimeError("Configured n8n origin must use HTTPS")
    with httpx.Client(
        base_url=values["N8N_ORIGIN"].rstrip("/") + "/api/v1/",
        headers={"X-N8N-API-KEY": values["NOEDAERI_API_KEY"]},
        timeout=30,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        endpoint = "workflows/" + values["N8N_RAYA_WORKFLOW_ID"]
        remote = require(client.get(endpoint))
        prepared = prepare(remote)
        if args.translation:
            prepared = prepare_translation(prepared)
        if not args.apply:
            print(
                "Prepared compute context and execution data policy"
                + (" with typed translation branch." if args.translation else ".")
            )
            print("Existing nodes, branch connections, prompts and credentials are preserved.")
            print("Read-only preview; no server or configuration changes.")
            return
        # Close the local gate before touching an already enabled workflow.
        runtime = decrypt(ROOT / "config/runtime.enc.env")
        if dict(runtime, **values).get("N8N_COMPUTE_CONTEXT_READY") == "1":
            raise RuntimeError(
                "Disable local readiness and drain jobs before modifying the workflow"
            )
        executing = require(
            client.get(
                "executions",
                params={"workflowId": remote["id"], "status": "running", "limit": 1},
            )
        )
        if executing.get("data"):
            raise RuntimeError("Workflow has running executions; wait before applying")
        root = ROOT / "tmp/integrations/n8n-backups"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as backup:
            json.dump(remote, backup, ensure_ascii=False, indent=2)
        fresh = require(client.get(endpoint))
        if fresh["versionId"] != remote["versionId"] or fresh["active"] != remote["active"]:
            raise RuntimeError("Workflow changed during preview; no update performed")
        if remote["active"]:
            require(client.post(endpoint + "/deactivate"))
            fresh = require(client.get(endpoint))
            if fresh["active"] or fresh["versionId"] != remote["versionId"]:
                raise RuntimeError("Workflow changed during deactivation; no update performed")
        payload = {k: prepared[k] for k in ("name", "nodes", "connections", "settings")}
        require(client.put(endpoint, json=payload))
        saved = require(client.get(endpoint))
        verify(saved, prepared)
        require(client.post(endpoint + "/activate", json={"versionId": saved["versionId"]}))
        activated = require(client.get(endpoint))
        verify(activated, prepared)
        if not activated["active"]:
            raise RuntimeError("Workflow did not activate; readiness remains disabled")
    print("Workflow patched, preserved and activated; private local backup retained.")
    print("Readiness unchanged. Verify a real delegated execution before enabling it.")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, KeyError, OSError, ValueError, RuntimeError) as error:
        # Exception strings from networking/JSON can contain addresses or payloads.
        reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print("Workflow update did not complete: " + reason)
        raise SystemExit(1) from None
