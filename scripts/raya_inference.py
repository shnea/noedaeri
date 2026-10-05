"""Private JSON-lines inference child. No server credentials or network access are needed."""

import json
import sys
import time
from pathlib import Path

ROUTE = {
    "type": "choice",
    "instructions": "Route this prompt to a model.",
    "criteria": {
        "L1": "simple requests",
        "L2": "moderately complex requests",
        "L3": "complex requests requiring multi-step reasoning",
        "L4": "very hard requests requiring advanced reasoning",
    },
}


def reply(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def main():
    import psutil

    if psutil.virtual_memory().available < int(sys.argv[2]):
        reply({"error": "raya_memory_unavailable"})
        return
    # Redirect library diagnostics away from the JSON protocol and request logs.
    protocol = sys.stdout
    sys.stdout = sys.stderr
    from laya.common import build_sequence, serialize_state
    from laya.onnx_agent import ONNXAgent

    folder = Path(sys.argv[1])
    manifest = json.loads((folder / "manifest.json").read_text())
    agent = ONNXAgent(str(folder), onnx_path=str(folder / "onnx/raya.onnx"))
    agent.cfg["max_len"] = 512
    sys.stdout = protocol
    if psutil.virtual_memory().available < 1024**3:
        reply({"error": "raya_memory_unavailable"})
        return
    reply({"ready": True})
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            state = {"prompt": payload["prompt"]}
            if payload.get("has_images"):
                state["has_images"] = True
            if payload.get("instruction"):
                state["instruction"] = payload["instruction"]
            started = time.perf_counter()
            state_ids = agent.tok(
                serialize_state(state).replace(agent.tok.mask_token, " "),
                add_special_tokens=False,
            )["input_ids"]
            head, _ = build_sequence(
                agent.tok,
                state,
                agent._to_internal(ROUTE),
                512,
                agent.cfg.get("head_max_len", 192),
                state_ids=[],
            )
            out = agent.system_one(state, {"route": ROUTE}, max_len=512)
            answer = out["answers"]["route"]
            reply(
                {
                    "task_type": payload["task_type"],
                    "model_tier": answer["choice"],
                    "probabilities": answer["probabilities"],
                    "confidence": answer["confidence"],
                    "input_tokens": out["usage"]["input_tokens"],
                    "input_truncated": len(state_ids) > 512 - len(head),
                    "inference_ms": round((time.perf_counter() - started) * 1000, 2),
                    "model": manifest["model"],
                    "revision": manifest["revision"],
                    "device": "cpu",
                    "runtime": "onnx-fp32",
                }
            )
        except Exception:
            reply({"error": "raya_inference_failed"})
            return


if __name__ == "__main__":
    try:
        main()
    except MemoryError:
        reply({"error": "raya_memory_unavailable"})
    except Exception:
        reply({"error": "raya_loading_failed"})
