"""서빙 점검: 한국어 JSON schema 출력 3회 성공 여부와 응답 시간·GPU 메모리(serving_check.json), 모델 정보(model_card.json).

사용: GPU=0 python server/check_serving.py   (serve_qwen.sh 로 서버를 띄운 뒤)
"""
import json
import os
import re
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

SERVER = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER.parent / "lib"))
import llm

llm.LOG_PATH = SERVER / "check_calls.jsonl"
SEED = 0

SCHEMA = {
    "type": "object",
    "required": ["요구사항", "근거", "키워드"],
    "properties": {
        "요구사항": {"type": "string", "minLength": 10},
        "근거": {"type": "string", "minLength": 5},
        "키워드": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
PROMPTS = [
    "가상의 불면증 치료 앱에서 로그인 실패가 반복될 때의 요구사항 한 문장을 '~하여야 한다' 문체로 쓰세요.",
    "가상의 불면증 치료 앱에서 수면일기 저장에 관한 요구사항 한 문장을 쓰세요.",
    "가상의 의료기기 서버에서 감사로그 보호에 관한 요구사항 한 문장을 쓰세요.",
]


def gpu_mem(gpu):
    out = subprocess.run(
        ["nvidia-smi", f"--id={gpu}", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader,nounits"], capture_output=True, text=True, check=True).stdout.strip()
    idx, name, used, total, util = [x.strip() for x in out.split(",")]
    return {"gpu": int(idx), "name": name, "memory_used_mib": int(used), "memory_total_mib": int(total),
            "util_pct": int(util)}


def main():
    gpu = os.getenv("GPU", "0")
    results = []
    for i, p in enumerate(PROMPTS):
        t0 = time.perf_counter()
        try:
            v = llm.chat_json([{"role": "user", "content": p}], SCHEMA, prompt_version="gate1-v1",
                              task=f"gate1/{i}", temperature=0.2, seed=SEED, max_tokens=300)
            korean = bool(re.search(r"[가-힣]", v["요구사항"]))
            results.append({"ok": korean, "seconds": round(time.perf_counter() - t0, 3), "output": v})
        except Exception as err:
            results.append({"ok": False, "seconds": round(time.perf_counter() - t0, 3), "error": str(err)[:300]})
    mem = gpu_mem(gpu)
    snap = Path.home() / ".cache/huggingface/hub/models--Qwen--Qwen3-32B-FP8/snapshots"
    revision = sorted(p.name for p in snap.iterdir())[0] if snap.exists() else None
    log_head = (SERVER / "vllm.log").read_text(encoding="utf-8", errors="replace")
    starts = [l for l in log_head.splitlines() if " start: " in l]
    card = {
        "model_id": "Qwen/Qwen3-32B-FP8",
        "served_model_name": llm.server_info()["data"][0]["id"],
        "revision": revision,
        "precision": "FP8 weights (block-wise 128x128, official Qwen release), "
                     "kv-cache fp8_e4m3 (scale 1.0, no calibrated scales)",
        "vllm_version": version("vllm"),
        "engine": "V0 (vLLM falls back from V1 because --kv-cache-dtype fp8)",
        "gpu_count": 1,
        "gpu": mem["name"],
        "cuda_visible_devices": gpu,
        "max_model_len": 32768,
        "server_seed": 0,
        "request_seed_policy": "llm.py: seed per call (base seed + attempt), logged in runs/gen_log.jsonl",
        "thinking": "disabled via chat_template_kwargs.enable_thinking=false",
        "serve_command": starts[-1].split(" start: ", 1)[1] if starts else None,
    }
    (SERVER / "model_card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate = {"passed": all(r["ok"] for r in results) and len(results) == 3, "calls": results, "gpu_after": mem}
    (SERVER / "serving_check.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(gate, ensure_ascii=False, indent=1))
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
