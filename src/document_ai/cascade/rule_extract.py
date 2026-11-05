"""규칙 자동 추출: 코퍼스의 '적용 예외' 청크에서 LLM이 규칙 후보를 rules_table 형식으로 뽑는다(조항 ID 필수).

- 입력 청크: 코퍼스에서 field가 '예외'이거나 조항 ID가 '-예외'로 끝나는 청크만.
- 검증: 스키마(attr, attr_ko, equals bool, secs 목록, result ∈ {N/A, 적용}, clause_ids 비지 않음)와 조항 ID가 코퍼스에 실제로
  있는지. 통과 못 한 규칙은 버리고 이유를 기록한다.
- 출력: domain_packs/<팩>/rules_table_extracted.yaml(규칙 + 판단 칸·N/A 문구 같은 문서 양식 필드는 curated 표에서 복사),
  사람 검수용 rules_review.csv(규칙, 근거 조항 원문 일부, 검수 칸).
- rules_source = curated(사람 대신 Claude Code가 원문으로 쓴 v2) | extracted(이 모듈 결과) | retrieval_only(규칙 표 없이
  검색 조항만 맥락에 제공).
사용: python -m document_ai.cascade.rule_extract [--base-url http://127.0.0.1:8100/v1]
"""

import argparse
import csv
import json
import re
from pathlib import Path

from . import pack as pack_mod
from .llm import load_prompt
from .retrieval import load_corpus

PROMPT = "rule_extract_v1"
SOURCES = ("curated", "extracted", "retrieval_only")
EXTRACTED = "rules_table_extracted.yaml"
SCHEMA = {"type": "object", "required": ["rules"], "properties": {"rules": {"type": "array", "items": {
    "type": "object", "required": ["attr", "attr_ko", "equals", "secs", "result", "clause_ids"],
    "properties": {"attr": {"type": "string"}, "attr_ko": {"type": "string"}, "equals": {"type": "boolean"},
                   "secs": {"type": "array", "items": {"type": "string"}}, "result": {"type": "string"},
                   "clause_ids": {"type": "array", "items": {"type": "string"}}}}}}}


class RuleError(ValueError):
    pass


def exception_chunks(chunks):
    return [c for c in chunks if c.get("field") == "예외" or c["clause_id"].endswith("-예외")]


def validate_rule(r, clause_ids, sec_re=re.compile(r"^[A-Z]{1,5}-\d{1,4}$")):
    """sec_re 기본값은 범용 '대문자-숫자' 모양. extract는 팩의 연결 항목 형식을 넘긴다."""
    if not re.match(r"^[a-z][a-z0-9_]*$", str(r.get("attr", ""))):
        raise RuleError(f"attr 형식: {r.get('attr')!r}")
    if not isinstance(r.get("equals"), bool):
        raise RuleError("equals는 true/false")
    if r.get("result") not in ("N/A", "적용"):
        raise RuleError(f"result: {r.get('result')!r}")
    if not r.get("secs") or not all(sec_re.match(s) for s in r["secs"]):
        raise RuleError(f"secs: {r.get('secs')!r}")
    if not r.get("clause_ids"):
        raise RuleError("clause_ids 비어 있음")
    missing = [c for c in r["clause_ids"] if c not in clause_ids]
    if missing:
        raise RuleError(f"코퍼스에 없는 조항 ID: {missing}")


def extract(pk, llm, out_dir=None, reader=None):
    pk = pack_mod.load(pk)
    chunks, h = load_corpus(pk.corpus_dir(), pk.corpus["regs"], pk.corpus["hash"], reader)
    exc = exception_chunks(chunks)
    clause_ids = {c["clause_id"] for c in chunks}
    system, tpl = load_prompt(PROMPT, pk)
    user = tpl.format(clauses="\n".join(f"- [{c['clause_id']}] {c['section']}: {c['text']}" for c in exc))
    out, usage = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                               SCHEMA, "rule_extract")
    keep, dropped = [], []
    for r in out["rules"]:
        try:
            validate_rule(r, clause_ids, pk.sec_full_re)
        except RuleError as err:
            dropped.append({"attr": r.get("attr"), "reason": str(err)})
            continue
        texts = {c["clause_id"]: c["text"] for c in exc}
        keep.append({"id": f"X{len(keep) + 1:02d}", **{k: r[k] for k in ("attr", "attr_ko", "equals", "secs", "result",
                                                                           "clause_ids")},
                     "aliases": [r["attr_ko"]], "source": " ".join(texts.get(c, "") for c in r["clause_ids"])[:300]})
    curated = pk.rules_doc()
    doc = {"version": "rules_table_extracted", "corpus_sha256": h, "model": getattr(llm, "model", ""),
           "rules": keep, "notes": [],
           **{k: curated[k] for k in ("decision_cells", "na_texts", "applied_texts")}}
    report = {"n_chunks": len(exc), "n_rules": len(keep), "dropped": dropped, "usage": usage}
    if out_dir is not None:
        write(doc, Path(out_dir), exc)
    return doc, report


def write(doc, out_dir, exc):
    out_dir.mkdir(parents=True, exist_ok=True)
    head = ("# 자동 추출 규칙 표(rule_extract.py, 사람 검수 전). 근거: 코퍼스 적용 예외 청크.\n"
            "# 판단 칸·N/A 문구 같은 문서 양식 필드는 curated 표에서 복사했다(규정 내용이 아님).\n")
    (out_dir / EXTRACTED).write_text(head + json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    texts = {c["clause_id"]: c["text"] for c in exc}
    with (out_dir / "rules_review.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule", "attr", "attr_ko", "equals", "secs", "result", "clause_ids", "clause_text", "review"])
        for r in doc["rules"]:
            w.writerow([r["id"], r["attr"], r["attr_ko"], r["equals"], " ".join(r["secs"]), r["result"],
                        " ".join(r["clause_ids"]), " / ".join(texts.get(c, "")[:120] for c in r["clause_ids"]), ""])


def with_rules_source(pk, source):
    if source not in SOURCES:
        raise ValueError(f"rules_source must be one of {SOURCES}")
    if source == "curated":
        return pk
    raw = dict(pk.raw)
    raw["rules_table"] = EXTRACTED if source == "extracted" else ""
    if source == "retrieval_only":
        # 규칙은 없지만 판단 칸·N/A 문구(문서 양식)는 남긴다: 규칙 표 파일 대신 빈 규칙 문서를 쓴다
        raw["rules_table"] = ""
    new = pack_mod.DomainPack(raw, pk.root)
    if source == "retrieval_only":
        keep = pk.rules_doc()
        new.rules_doc = lambda reader=None: {"rules": [], "notes": keep.get("notes", []),
                                             **{k: keep[k] for k in ("decision_cells", "na_texts", "applied_texts")}}
    return new


def compare(curated, extracted):
    """규칙 단위 일치도. 속성 키 이름은 서로 달라도 되고, (보안 항목, equals, result) 단위로 맞춘다."""
    def units(rules):
        return {(s, r["equals"], r["result"]) for r in rules for s in r["secs"]}
    cu, eu = units(curated), units(extracted)
    tp = len(cu & eu)
    exact = sum(1 for r in curated if any(set(r["secs"]) == set(x["secs"]) and r["equals"] == x["equals"]
                                          and r["result"] == x["result"] for x in extracted))
    return {"sec_level": {"precision": round(tp / len(eu), 4) if eu else None,
                          "recall": round(tp / len(cu), 4) if cu else None, "n_curated": len(cu), "n_extracted": len(eu)},
            "exact_rules": exact, "n_curated_rules": len(curated), "n_extracted_rules": len(extracted),
            "missing": sorted(cu - eu), "extra": sorted(eu - cu)}


def main():
    from .llm import LLMClient
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8100/v1")
    ap.add_argument("--model", default="qwen3-32b")
    ap.add_argument("--pack", default="ec_sw")
    a = ap.parse_args()
    pk = pack_mod.load(a.pack)
    llm = LLMClient(a.base_url, a.model, 0.0, 20261103, 4096)
    doc, report = extract(pk, llm, out_dir=pk.root)
    cmp_ = compare(pk.rules_doc()["rules"], doc["rules"])
    (pk.root / "rules_extract_report.json").write_text(json.dumps({"report": report, "compare_to_curated": cmp_},
                                                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"n_rules": report["n_rules"], "dropped": report["dropped"], "compare": cmp_}, ensure_ascii=False))


if __name__ == "__main__":
    main()
