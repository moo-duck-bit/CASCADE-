"""개발셋 조정을 마친 설정을 고정한다(v3). full 프리셋 설정 해시, 프롬프트·규칙 표(curated, extracted)·검사 명세·도메인 팩·
단가표 파일 해시, 편집 연산 스키마 해시, 검색 설정(검색기, k, 임베딩 모델 revision), 코퍼스 해시를 FROZEN.json에 쓴다.
v3부터 패키지 파이썬 코드 전체의 해시(code_sha256)도 쓴다(v2는 FROZEN_v2.json, v1은 FROZEN_v1.json).

v0.3 평가 전에 `python -m document_ai.cascade.freeze --check`로 고정 이후 바뀐 것이 없는지 확인한다.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import config as cfg

PKG = Path(__file__).resolve().parent
FROZEN = PKG / "FROZEN.json"
FILES = ["domain_packs/ec_sw/pack.yaml", "domain_packs/ec_sw/rules_table.yaml",
         "domain_packs/ec_sw/rules_table_extracted.yaml", "domain_packs/generic_text/pack.yaml",
         "verifier_spec.md", "prices.yaml"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _embed_revision():
    ref = Path.home() / ".cache/huggingface/hub/models--BAAI--bge-m3/refs/main"
    return ref.read_text().strip() if ref.exists() else "unknown"


def code_sha256():
    """패키지의 .py 파일 전부(상대 경로 순)를 이어 해시한다."""
    h = hashlib.sha256()
    for p in sorted(PKG.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        h.update(str(p.relative_to(PKG)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _corpus():
    from . import pack as pack_mod
    pk = pack_mod.load("ec_sw")
    d = pk.corpus_dir()
    fixed = (d / pk.corpus["hash"]).read_text(encoding="utf-8").split()[0]
    return {"dir": str(d), "sha256": sha(d / pk.corpus["regs"]), "fixed": fixed}


def snapshot(note=""):
    from .ops import OPS_SCHEMA
    from .retrieval import EMBED_MODEL
    full = cfg.preset("full")
    files = FILES + sorted(f"prompts/{p.name}" for p in (PKG / "prompts").glob("*.txt"))
    return {
        "version": "frozen_v3",
        "frozen_on": "v0.2 dev 36 cases (dev2) + multi-change requests for v0.3",
        "note": note,
        "config": full.to_dict(),
        "config_hash": full.hash(),
        "presets": {p: cfg.preset(p).hash() for p in cfg.PRESETS},
        "files": {f: sha(PKG / f) for f in files},
        "ops_schema_sha256": hashlib.sha256(json.dumps(OPS_SCHEMA, sort_keys=True).encode()).hexdigest(),
        "code_sha256": code_sha256(),
        "retrieval": {"retriever": full.retriever, "k": full.retrieval_k, "embed_model": EMBED_MODEL,
                      "embed_revision": _embed_revision()},
        "corpus": _corpus(),
    }


def _frozen():
    return json.loads(FROZEN.read_text(encoding="utf-8")) if FROZEN.exists() else None


def check():
    want = _frozen()
    if want is None:
        return ["FROZEN.json not found"]
    now = snapshot()
    diffs = []
    if want["config_hash"] != now["config_hash"]:
        diffs.append(f"config_hash {want['config_hash']} != {now['config_hash']}")
    for f, h in want["files"].items():
        if now["files"].get(f) != h:
            diffs.append(f"{f} changed")
    for k in ("ops_schema_sha256", "code_sha256"):
        if want.get(k) != now.get(k):
            diffs.append(f"{k} changed")
    if want.get("retrieval") != now.get("retrieval"):
        diffs.append("retrieval settings changed")
    wc, nc = want.get("corpus", {}), now.get("corpus", {})
    if wc.get("sha256") != nc.get("sha256") or nc.get("sha256") != nc.get("fixed"):
        diffs.append(f"corpus hash {nc.get('sha256', '')[:12]} != frozen {wc.get('sha256', '')[:12]}")
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    if a.check:
        diffs = check()
        print("frozen state ok" if not diffs else "\n".join(diffs))
        sys.exit(1 if diffs else 0)
    FROZEN.write_text(json.dumps(snapshot(a.note), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {FROZEN}")


if __name__ == "__main__":
    main()
