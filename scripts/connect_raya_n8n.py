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
            elif node["name"] == "L1 · OpenRouter Model" and values.get("N8N_OPENROUTER_CREDENTIAL_ID"):
                node["credentials"] = {
                    "openRouterApi": {
                        "id": values["N8N_OPENROUTER_CREDENTIAL_ID"],
                        "name": "뇌대리 OpenRouter",
                    }
                }
            elif node["name"] == "L2 · Groq Model" and values.get("N8N_GROQ_CREDENTIAL_ID"):
                node["credentials"] = {
                    "groqApi": {
                        "id": values["N8N_GROQ_CREDENTIAL_ID"],
                        "name": "뇌대리 Groq",
                    }
                }
            elif node["name"] == "L3 · Gemini Model" and values.get("N8N_GEMINI_CREDENTIAL_ID"):
                node["credentials"] = {
                    "googlePalmApi": {
                        "id": values["N8N_GEMINI_CREDENTIAL_ID"],
                        "name": "뇌대리 Google AI Studio",
                    }
                }
            elif node["name"] == "폴백 · Mistral Model" and values.get("N8N_MISTRAL_CREDENTIAL_ID"):
                node["credentials"] = {
                    "mistralCloudApi": {
                        "id": values["N8N_MISTRAL_CREDENTIAL_ID"],
                        "name": "뇌대리 Mistral",
                    }
                }
            elif node["name"] in {"포트폴리오 검색 임베딩", "Google Gemini 임베딩 (공통)", "Gemini 임베딩"} and values.get("N8N_GEMINI_CREDENTIAL_ID"):
                node["credentials"] = {
                    "googlePalmApi": {
                        "id": values["N8N_GEMINI_CREDENTIAL_ID"],
                        "name": "뇌대리 Google AI Studio",
                    }
                }
            elif node["name"] == "포트폴리오 벡터 검색" and values.get("N8N_QDRANT_CREDENTIAL_ID"):
                node["credentials"] = {
                    "qdrantApi": {
                        "id": values["N8N_QDRANT_CREDENTIAL_ID"],
                        "name": "뇌대리 Qdrant",
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
        connections.pop("Raya·AI 연결 예정", None)
        connections.pop("포트폴리오 검색 · 지침 대기", None)
        for name in retired_names:
            connections.pop(name, None)
        for source, groups in template["connections"].items():
            connections[source] = groups
        valid_names = {node["name"] for node in nodes}
        connections = {k: v for k, v in connections.items() if k in valid_names}
        for groups in connections.values():
            for conn_list in groups.values():
                for output in conn_list:
                    output[:] = [edge for edge in output if edge["node"] in valid_names]
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
            actual_params = actual[node["id"]]["parameters"]
            for k, v in node["parameters"].items():
                if actual_params.get(k) != v:
                    raise RuntimeError(f"Saved node parameter {k} did not match on node {node['name']}")
            if actual[node["id"]]["name"] != node["name"]:
                raise RuntimeError(f"Saved node name did not match on node {node['name']}")
    print("Inactive n8n draft updated and verified; original instructions preserved.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, httpx.HTTPError):
        raise SystemExit(
            "Raya n8n connection failed; credentials and server details withheld."
        ) from None
