"""BGE-M3 임베딩 서버(CASCADE 밀집 검색의 질의 임베딩용)와 코퍼스 청크 임베딩 캐시 만들기.

conda env e0_dense(FlagEmbedding)에서 돈다. 모델은 로컬 HF 캐시의 BAAI/bge-m3(오프라인).
- 서버: python server/embed_server.py serve --port 8101    POST /embed {"texts": [...]} → {"vectors": [...]}
- 캐시: python server/embed_server.py cache                 corpus/cache/bge-m3_<해시12>.jsonl (git 제외)
GPU는 CUDA_VISIBLE_DEVICES로 빈 장치를 지정한다.
"""
import argparse
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
MODEL = "BAAI/bge-m3"


def load_model():
    from FlagEmbedding import BGEM3FlagModel
    return BGEM3FlagModel(MODEL, use_fp16=True)


def embed(model, texts):
    out = model.encode(texts, batch_size=16, max_length=1024, return_dense=True, return_sparse=False,
                       return_colbert_vecs=False)["dense_vecs"]
    return [[round(float(x), 6) for x in v] for v in out]


def revision():
    snap = Path.home() / ".cache/huggingface/hub/models--BAAI--bge-m3/refs/main"
    return snap.read_text().strip() if snap.exists() else "unknown"


def build_cache(model):
    data = (CORPUS / "regs.jsonl").read_bytes()
    h = hashlib.sha256(data).hexdigest()
    chunks = [json.loads(ln) for ln in data.decode("utf-8").splitlines() if ln.strip()]
    vecs = embed(model, [f"{c.get('section', '')} {c['text']}" for c in chunks])
    out = CORPUS / "cache" / f"bge-m3_{h[:12]}.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for c, v in zip(chunks, vecs):
            f.write(json.dumps({"chunk_id": c["chunk_id"], "v": v}) + "\n")
    (CORPUS / "cache" / "embed_meta.json").write_text(json.dumps(
        {"model": MODEL, "revision": revision(), "corpus_sha256": h, "n": len(chunks)}, indent=1), encoding="utf-8")
    print(out, len(vecs), "revision", revision())


def serve(model, port):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.dumps({"vectors": embed(model, body["texts"])}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    print(f"embed server on 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["serve", "cache"])
    ap.add_argument("--port", type=int, default=8101)
    a = ap.parse_args()
    m = load_model()
    build_cache(m) if a.cmd == "cache" else serve(m, a.port)
