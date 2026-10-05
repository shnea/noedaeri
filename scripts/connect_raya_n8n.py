"""Attach the Raya stages to an existing draft using encrypted connection settings."""

import json
import os
import uuid
from io import StringIO
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from manage import ROOT, decrypt, execute, sops_env


def save_encrypted(path, values):
    plaintext = "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items()).encode()
    encrypted = execute(
        [
            "sops",
            "encrypt",
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            "--filename-override",
            str(path.relative_to(ROOT)),
        ],
        input=plaintext,
        env=sops_env(),
    ).stdout
    checked = execute(
        ["sops", "decrypt", "--input-type", "dotenv", "--output-type", "dotenv"],
        input=encrypted,
        env=sops_env(),
    ).stdout
    if dotenv_values(stream=StringIO(checked.decode()), interpolate=False) != values:
        raise RuntimeError("Encrypted settings verification failed")
    staged = path.with_suffix(path.suffix + ".write")
    try:
        with staged.open("wb") as output:
            os.chmod(staged, 0o600)
            output.write(encrypted)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def require(response):
    if response.status_code not in (200, 201):
        raise RuntimeError(f"n8n API rejected request (HTTP {response.status_code})")
    return response.json()


def main():
    config = ROOT / "config/n8n.enc.env"
    values = decrypt(config)
    runtime = decrypt(ROOT / "config/runtime.enc.env")
    origin = urlsplit(values["N8N_ORIGIN"])
    if origin.scheme != "https" or origin.username or origin.password or origin.query:
        raise RuntimeError("n8n requires a fixed HTTPS origin")
    workflow_id = values["N8N_RAYA_WORKFLOW_ID"]
    template = json.loads((ROOT / "examples/n8n_ai_routing_sample.json").read_text())
    with httpx.Client(
        base_url=values["N8N_ORIGIN"].rstrip("/") + "/api/v1/",
        headers={"X-N8N-API-KEY": values["NOEDAERI_API_KEY"]},
        timeout=30,
    ) as client:
        remote = require(client.get("workflows/" + workflow_id))
        if remote["active"]:
            raise RuntimeError("Only the inactive draft may be changed")
        reference = next(node for node in template["nodes"] if node["name"] == "Raya 요청 준비")
        if not any(node["id"] == reference["id"] for node in remote["nodes"]):
            raise RuntimeError("Selected workflow does not contain the original Raya placeholder")
        if not values.get("N8N_RAYA_CREDENTIAL_ID"):
            credential = require(
                client.post(
                    "credentials",
                    json={
                        "name": "뇌대리 Raya 전용",
                        "type": "httpHeaderAuth",
                        "data": {
                            "name": "X-Noedaeri-Raya-Key",
                            "value": runtime["NOEDAERI_RAYA_API_KEY"],
                        },
                    },
                )
            )
            values["N8N_RAYA_CREDENTIAL_ID"] = credential["id"]
            save_encrypted(config, values)
        for node in template["nodes"]:
            if node["name"] == "Raya 난이도 판단":
                node["parameters"]["url"] = (
                    runtime["PUBLIC_ORIGIN"].rstrip("/") + "/api/ai/v1/raya/route"
                )
                node["credentials"] = {
                    "httpHeaderAuth": {
                        "id": values["N8N_RAYA_CREDENTIAL_ID"],
                        "name": "뇌대리 Raya 전용",
                    }
                }
        # Preserve input samples, task instructions, custom nodes and existing branch rules.
        expected_ids = {node["id"] for node in template["nodes"]}
        retired_ids = {str(uuid.uuid5(uuid.NAMESPACE_URL, "noedaeri.example/draft/l4-output"))}
        unchanged = {"n8n-nodes-base.manualTrigger", "n8n-nodes-base.set"}
        remote_by_id = {node["id"]: node for node in remote["nodes"]}
        nodes = []
        for node in template["nodes"]:
            original = remote_by_id.get(node["id"])
            if original and (
                node["type"] in unchanged
                or node["name"]
                in {
                    "샘플 요청 8종 + 미등록",
                    "작업 종류 분기",
                }
            ):
                nodes.append(original)
            else:
                nodes.append(node)
        nodes.extend(
            node
            for node in remote["nodes"]
            if node["id"] not in expected_ids | retired_ids
        )
        retired_names = {node["name"] for node in remote["nodes"] if node["id"] in retired_ids}
        connections = remote["connections"]
        connections.pop("공급자·하향 후보 준비", None)
        for name in retired_names:
            connections.pop(name, None)
        for groups in connections.values():
            for output in groups.get("main", []):
                output[:] = [edge for edge in output if edge["node"] not in retired_names]
                for edge in output:
                    if edge["node"] == "Raya·AI 연결 예정":
                        edge["node"] = "Raya 요청 준비"
        connections.pop("Raya·AI 연결 예정", None)
        for source, groups in template["connections"].items():
            if source in {
                "Raya 요청 준비",
                "Raya 난이도 판단",
                "요청·판단 합치기",
                "공급자·순환 후보 준비",
                "공급자 경로 · 한도 연결 대기",
                "모델 성능 등급 분기",
            }:
                connections[source] = groups
        fresh = require(client.get("workflows/" + workflow_id))
        if fresh["versionId"] != remote["versionId"] or fresh["active"]:
            raise RuntimeError("Workflow changed during setup; no workflow update performed")
        payload = {
            "name": remote["name"],
            "nodes": nodes,
            "connections": connections,
            "settings": remote["settings"],
        }
        require(client.put("workflows/" + workflow_id, json=payload))
        saved = require(client.get("workflows/" + workflow_id))
        if saved["active"] or saved["connections"] != connections:
            raise RuntimeError("Workflow verification failed")
        actual = {node["id"]: node for node in saved["nodes"]}
        if set(actual) != {node["id"] for node in nodes}:
            raise RuntimeError("Saved node set did not match")
        for node in nodes:
            if (
                actual[node["id"]]["parameters"] != node["parameters"]
                or actual[node["id"]]["name"] != node["name"]
            ):
                raise RuntimeError("Saved node parameters did not match")
    print("Inactive n8n draft updated and verified; original instructions preserved.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, httpx.HTTPError):
        raise SystemExit(
            "Raya n8n connection failed; credentials and server details withheld."
        ) from None
