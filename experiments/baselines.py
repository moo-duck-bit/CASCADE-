"""베이스라인 B1(문서 전체), B2(B1 + 고정 규칙 문구), B3(BM25 상위 10칸)의 프롬프트 조립과 모델 호출.

run1.py, run2.py가 import 해서 쓴다. B1 형식은 pilot_base paper/baselines.py 를 참고해 새로 작성했다.
"""
import json
import math
import re
import sys
import threading
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scoring"))
# 케이스 읽기는 채점기와 같은 함수를 쓴다. run1·run2·current_pipeline도 여기서 가져간다.
from score import CASES, SEED, load_cases, read_input

PROMPTS = ROOT / "experiments" / "prompts"
OUT = ROOT / "results" / "run1" / "predictions"
CALLS = ROOT / "results" / "run1" / "calls_baselines.jsonl"
BASE_URL = "http://127.0.0.1:8100/v1"
MODEL = "qwen3-32b"
MAX_TOKENS = 6000
TOP_K = 10
PROMPT_VERSION = {"B1": "run1-b1-v1", "B2": "run1-b2-v1", "B3": "run1-b3-v1"}

SYSTEM = """당신은 의료기기 소프트웨어 문서 세트(MDSR 요구사항 명세서, MDDR 설계 명세서, XXCS 소프트웨어 보안 검증 보고서)를 관리하는 문서 담당자입니다.
변경 요청이 하나 들어왔을 때, 주어진 문서 안에서 함께 수정해야 하는 모든 칸을 찾아 새 내용을 작성합니다.

규칙:
- 문서의 각 줄 앞 `[문서 | 항목 | 칸]` 머리표가 위치입니다. doc, item, field에는 머리표의 세 부분을 그대로 씁니다.
- 없는 위치를 만들지 않습니다. 단, 요청이 새 요구사항 추가이면 그 요구사항의 칸만 같은 형식으로 새로 씁니다.
- 수정이 필요 없는 칸은 넣지 않습니다. 바꿀 곳이 없으면 빈 배열을 냅니다.
- new_text에는 그 칸의 수정 후 전체 내용을 씁니다.
- 출력은 JSON 객체 하나만 냅니다."""

B3_NOTE = "아래 문서는 전체 문서 세트 중 변경 요청과 관련 있어 보이는 칸 일부만 검색해 보여 준 것입니다."
OUTPUT_FMT = '출력 형식: {"edits": [{"doc": "MDSR|MDDR|XXCS", "item": "항목", "field": "칸", "new_text": "수정 후 내용"}]}'
FORMAT_FIX = '출력을 JSON으로 읽을 수 없습니다. 내용은 바꾸지 말고 {"edits": [...]} 형식만 고쳐서 다시 출력하세요.'

SCHEMA = {
    "type": "object", "required": ["edits"], "additionalProperties": False,
    "properties": {"edits": {"type": "array", "items": {
        "type": "object", "required": ["doc", "item", "field", "new_text"], "additionalProperties": False,
        "properties": {"doc": {"type": "string"}, "item": {"type": "string"}, "field": {"type": "string"},
                       "new_text": {"type": "string"}}}}},
}
PARSE_ERRORS = (ValueError, KeyError, json.JSONDecodeError)
CELL_RE = re.compile(r"^\[(MDSR|MDDR|XXCS) \| [^\]]+\]")
_lock = threading.Lock()


def char_ngrams(text, ns=(2, 3)):
    t = re.sub(r"\s+", " ", text.lower())
    return [t[i:i + n] for n in ns for i in range(len(t) - n + 1) if t[i:i + n].strip()]


def bm25_top(cells, query, k=TOP_K, k1=1.5, b=0.75):
    """칸 단위 BM25(문자 2·3-gram). 점수가 같으면 앞 칸이 먼저."""
    docs = [Counter(char_ngrams(c)) for c in cells]
    n = len(docs)
    avgdl = sum(sum(d.values()) for d in docs) / max(n, 1)
    df = Counter(g for d in docs for g in d)
    q = Counter(char_ngrams(query))
    scores = []
    for i, d in enumerate(docs):
        dl = sum(d.values())
        s = 0.0
        for g in q:
            if g not in d:
                continue
            idf = math.log(1 + (n - df[g] + 0.5) / (df[g] + 0.5))
            tf = d[g]
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
        scores.append((s, -i))
    return sorted(range(n), key=lambda i: scores[i], reverse=True)[:k]


def build_messages(method, cid):
    """system → 문서 → 요청(맨 뒤). 같은 방법·같은 문서 조합끼리 prefix 캐시가 걸린다."""
    index_md = read_input(cid, "site_index.md")
    request = read_input(cid, "request.txt").strip()
    info = {}
    system = SYSTEM
    if method == "B2":
        system += "\n\n" + (PROMPTS / "b2_rules_v1.txt").read_text(encoding="utf-8").strip()
    if method == "B3":
        cells = [ln for ln in index_md.splitlines() if CELL_RE.match(ln)]
        top = sorted(bm25_top(cells, request))  # 문서 순서 유지
        docs_text = B3_NOTE + "\n" + "\n".join(cells[i] for i in top)
        info["retrieved"] = [CELL_RE.match(cells[i]).group(0) for i in top]
    else:
        docs_text = index_md
    user = f"## 문서\n{docs_text}\n\n## 변경 요청\n{request}\n\n{OUTPUT_FMT}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], info


def post(messages, model=MODEL, temperature=0.0, seed=SEED, schema=SCHEMA, schema_name="edits"):
    body = {"model": model, "messages": messages, "temperature": temperature, "seed": seed, "max_tokens": MAX_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "schema": schema, "strict": True}}}
    req = urllib.request.Request(f"{BASE_URL}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as r:
        return json.loads(r.read())


def parse(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    data = json.loads(cleaned)
    edits = data["edits"] if isinstance(data, dict) else data
    if not isinstance(edits, list):
        raise ValueError("edits is not a list")
    return [e for e in edits if isinstance(e, dict)]


def format_retry_messages(messages, text):
    return messages + [{"role": "assistant", "content": text}, {"role": "user", "content": FORMAT_FIX}]


def run_one(method, cid, meta):
    """B1/B2/B3 한 케이스. JSON을 못 읽으면 형식만 고치라고 한 번 더 묻는다(내용 재검토 요청은 하지 않는다)."""
    out_path = OUT / method / f"{cid}.json"
    if out_path.exists():
        return
    messages, info = build_messages(method, cid)
    rec = {"case_id": cid, "case_type": meta["case_type"], "method": method, "model": MODEL,
           "prompt_version": PROMPT_VERSION[method], "seed": SEED, "input_tokens": 0, "output_tokens": 0,
           "seconds": 0.0, "parse_ok": False, "n_pred": 0, "truncated": False, "error": None, "format_retry": False}
    edits, responses = [], []

    def ask(msgs, seed):
        r = post(msgs, seed=seed)
        text = r["choices"][0]["message"]["content"] or ""
        responses.append(text)
        rec["input_tokens"] += r["usage"]["prompt_tokens"]
        rec["output_tokens"] += r["usage"]["completion_tokens"]
        rec["truncated"] = rec["truncated"] or r["choices"][0].get("finish_reason") == "length"
        return text

    t0 = time.perf_counter()
    try:
        text = ask(messages, SEED)
        try:
            edits = parse(text)
            rec["parse_ok"] = True
        except PARSE_ERRORS:
            rec["format_retry"] = True
            text = ask(format_retry_messages(messages, text), SEED + 1)
            try:
                edits = parse(text)
                rec["parse_ok"] = True
            except PARSE_ERRORS as err:
                rec["error"] = f"parse: {err}"[:300]
    except Exception as err:  # 서버 오류도 결과로 남긴다
        rec["error"] = f"{type(err).__name__}: {err}"[:300]
    rec["seconds"] = round(time.perf_counter() - t0, 2)
    rec["n_pred"] = len(edits)
    if rec["error"] and not responses:  # 서버 연결 실패는 저장하지 않아 다음 실행에서 다시 돈다
        print("ERROR", method, cid, rec["error"], flush=True)
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = {**rec, "edits": edits, "responses": responses, **info}
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    with _lock, CALLS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"{method} {cid} in={rec['input_tokens']} out={rec['output_tokens']} {rec['seconds']}s "
          f"parse={rec['parse_ok']} n={rec['n_pred']} trunc={rec['truncated']} err={rec['error']}", flush=True)


def append_log(path, msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def doc_order(cids, metas):
    """같은 문서 조합끼리 붙여 보내 prefix 캐시가 잘 걸리게 한다."""
    return sorted(cids, key=lambda c: (",".join(metas[c]["docs"]), c))


def run_timed(tag, cids, metas, fn, out_dir, log, eta_hook=None):
    """결과 파일이 없는 케이스를 동시 2개씩 돌린다. 처음 5케이스 벽시계 시간으로 남은 시간을 추정해 log에 남긴다."""
    todo = doc_order([c for c in cids if not (out_dir / tag / f"{c}.json").exists()], metas)
    log(f"{tag}: {len(cids) - len(todo)} done already, {len(todo)} to run")
    t0 = time.time()
    first, rest = todo[:5], todo[5:]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda c: fn(c, metas[c]), first))
    if first:
        per_case = (time.time() - t0) / len(first)
        log(f"{tag}: first {len(first)} cases {time.time() - t0:.0f}s wall, {per_case:.0f}s/case "
            f"→ remaining {len(rest)} ≈ {per_case * len(rest) / 60:.0f} min")
        if eta_hook:
            eta_hook(per_case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda c: fn(c, metas[c]), rest))
    log(f"{tag}: finished in {(time.time() - t0) / 60:.1f} min")
