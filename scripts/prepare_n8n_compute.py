"""Prepare an offline workflow export; never contact n8n or overwrite its input."""

import argparse
import copy
import json
from pathlib import Path

HEADER = "X-Noedaeri-Compute-Token"
EXPRESSION = (
    "={{ $json.compute_context?.token || (() => { try { "
    "return $('웹훅 접수').item.json.body?.compute_context?.token || ''; "
    "} catch { return ''; } })() }}"
)
HTTP_STEPS = {"공통 벡터 검색", "Raya 난이도 판단"}
NORMALIZER_MARKER = "// noedaeri: omit compute context from downstream data"
EXECUTION_SETTINGS = {
    "saveDataSuccessExecution": "none",
    "saveDataErrorExecution": "none",
    "saveManualExecutions": False,
    "saveExecutionProgress": False,
}


def prepare(workflow):
    result = copy.deepcopy(workflow)
    nodes = result.get("nodes", [])
    if not any(n.get("name") == "웹훅 접수" for n in nodes):
        raise ValueError("workflow_webhook_node_missing")
    patched = set()
    for node in nodes:
        if node.get("name") not in HTTP_STEPS:
            continue
        if node.get("type") != "n8n-nodes-base.httpRequest":
            raise ValueError("workflow_step_type_changed")
        parameters = node["parameters"]
        if parameters.get("specifyHeaders") == "json":
            raise ValueError("workflow_json_headers_need_manual_merge")
        parameters["sendHeaders"] = True
        parameters["specifyHeaders"] = "keypair"
        headers = parameters.setdefault("headerParameters", {}).setdefault("parameters", [])
        headers[:] = [h for h in headers if h.get("name", "").lower() != HEADER.lower()]
        headers.append({"name": HEADER, "value": EXPRESSION})
        patched.add(node["name"])
    if patched != HTTP_STEPS:
        raise ValueError("workflow_compute_steps_missing")
    normalizer = next((n for n in nodes if n.get("name") == "요청 정규화"), None)
    if not normalizer or normalizer.get("type") != "n8n-nodes-base.code":
        raise ValueError("workflow_normalizer_missing")
    code = normalizer.get("parameters", {}).get("jsCode")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("workflow_normalizer_code_missing")
    if not code.startswith(NORMALIZER_MARKER):
        # The HTTP expressions read the original webhook input, not model-facing data.
        normalizer["parameters"]["jsCode"] = (
            NORMALIZER_MARKER
            + "\nconst normalized = await (async () => {\n"
            + code
            + "\n})();\nreturn normalized.map(item => {\n"
            "  const { compute_context, ...json } = item.json;\n"
            "  return { ...item, json };\n});"
        )
    # The initial webhook data contains the short-lived lease; don't persist node payloads.
    result.setdefault("settings", {}).update(EXECUTION_SETTINGS)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("Input export must remain unchanged")
    try:
        prepared = prepare(json.loads(args.input.read_text()))
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exports may contain environment references/credentials: keep output outside tracked files.
        with args.output.open("x", encoding="utf-8") as target:
            target.write(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n")
        args.output.chmod(0o600)
    except (OSError, ValueError, KeyError):
        raise SystemExit("Workflow export could not be prepared; no server changes made") from None
    print("Prepared internal headers and context isolation. Import and validation remain pending.")


if __name__ == "__main__":
    main()
