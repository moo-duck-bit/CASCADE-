"""P6: 고정 코퍼스 규정 검색. 코퍼스 해시, 청크 필드, 검색기 3종 인터페이스, retrieval off면 호출 0, 가드 허용, 사용처."""

import os
import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade import pack as pack_mod
from document_ai.cascade import retrieval as R
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.run import run_case

from .fixture_docs import build_case

CORPUS = Path(os.environ.get("CASCADE_CORPUS_DIR", str(Path(__file__).resolve().parents[2] / "corpus")))
need_corpus = pytest.mark.skipif(not (CORPUS / "regs.jsonl").exists(), reason="corpus not built")
T2 = "제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다. 첨부3 적용 예외에 따라 문서를 갱신한다."
T2_UNKNOWN = "제품 사이버보안 속성 '블루투스 연결 사용'(bluetooth_link)을 false에서 true로 변경한다. 첨부3 적용 예외에 따라 문서를 갱신한다."
ADD = "모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 이메일 인증 후 비밀번호를 재설정한다."


def fake_embed(texts):
    """글자 해시 주머니 벡터(테스트용 임베딩)."""
    out = []
    for t in texts:
        v = [0.0] * 64
        for ch in t:
            v[hash(ch) % 64] += 1.0
        out.append(v)
    return out


@pytest.fixture(scope="module")
def chunks():
    return R.load_corpus(CORPUS)[0]


@need_corpus
def test_corpus_hash_fixed(tmp_path):
    chunks, h = R.load_corpus(CORPUS)
    assert h == (CORPUS / "regs.sha256").read_text().split()[0]
    bad = tmp_path / "corpus"
    shutil.copytree(CORPUS, bad, ignore=shutil.ignore_patterns("guides", "cache"))
    with (bad / "regs.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"chunk_id": "x"}\n')
    with pytest.raises(R.CorpusHashError):
        R.load_corpus(bad)


@need_corpus
def test_chunk_fields_and_guide3_security_split(chunks):
    for c in chunks:
        assert {"chunk_id", "clause_id", "source", "page", "section", "text"} <= set(c)
        assert c["source"] in ("첨부3", "첨부4", "첨부5") and isinstance(c["page"], int) and c["text"]
    ids = {c["clause_id"] for c in chunks}
    for f in ("세부요구사항", "시험기준", "시험방법", "예외"):
        assert f"첨부3-IA-05-{f}" in ids
    assert len(ids) == len(chunks)
    rows = list(csv.DictReader((CORPUS / "clause_ids.csv").open(encoding="utf-8")))
    assert {r["clause_id"] for r in rows} == ids


@need_corpus
@pytest.mark.parametrize("kind", ["bm25", "dense", "hybrid"])
def test_three_retrievers_same_interface(chunks, kind):
    bm = R.BM25Retriever(chunks)
    dense = R.DenseRetriever(chunks, fake_embed)
    r = {"bm25": bm, "dense": dense, "hybrid": R.HybridRetriever(bm, dense)}[kind]
    hits = r.search("비밀번호 기반 인증 기능이 없는 의료기기 적용 예외", 5)
    assert len(hits) == 5 and r.calls == 1
    assert set(hits[0]) == {"chunk_id", "clause_id", "source", "page", "section", "sec_id", "text", "score"}
    assert [h["score"] for h in hits] == sorted([h["score"] for h in hits], reverse=True)


@need_corpus
def test_bm25_finds_exception_clause(chunks):
    hits = R.BM25Retriever(chunks).search("비밀번호 기반 인증 기능이 없는 의료기기의 경우 적용하지 않는다", 3)
    assert hits[0]["clause_id"] == "첨부3-IA-05-예외"


def test_retrieval_config_flags():
    c = cfg.from_dict({"retrieval": True, "retriever": "bm25", "retrieval_k": 3})
    assert c.retrieval and c.retriever == "bm25" and c.retrieval_k == 3 and c.embed_url
    assert cfg.preset("full").retrieval is True and cfg.preset("full").retriever == "hybrid"
    assert cfg.preset("full").retrieval_k == 10
    assert cfg.preset("no_retrieval").retrieval is False


@need_corpus
def test_rules_table_v2_cites_guide3_exception_clauses_only():
    doc = pack_mod.load("ec_sw").rules_doc()
    assert doc["version"] == "rules_table_v2"
    ids = {r["clause_id"] for r in csv.DictReader((CORPUS / "clause_ids.csv").open(encoding="utf-8"))}
    attrs = {r["attr"] for r in doc["rules"]}
    assert len(attrs) >= 7
    for r in doc["rules"]:
        for cid in r["clause_ids"]:
            assert cid in ids and cid.startswith("첨부3-") and cid.endswith("-예외"), cid


def run_t2(tmp_path, request, conf, judge=None):
    inp = build_case(tmp_path / "case" / "input", request=request)
    seen = []

    def fn(messages, name):
        seen.append((name, messages))
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": judge(l) if judge else True, "reason": "t"}
                                  for i, l in enumerate(lines)]}
        tl = messages[1]["content"].split("## 고칠 칸")[1].split("##")[0]
        sites = [l.split("]")[0] + "]" for l in tl.splitlines() if l.startswith("[")]
        return {"edits": [{"site": s, "new_text": "보건의료정보 비식별화 기능을 확인한다."} for s in sites]}
    pred, log = run_case(inp, conf, llm=MockLLM(fn), work_dir=tmp_path / "w")
    return pred, log, seen


@need_corpus
def test_retrieval_off_makes_zero_calls(tmp_path):
    conf = cfg.from_dict({"retrieval": False, "edit_mode": "free_json"})
    pred, log, seen = run_t2(tmp_path, T2, conf)
    assert log["stages"]["retrieval"]["calls"] == 0
    assert all("관련 규정 근거" not in m[-1]["content"] for _, m in seen)


@need_corpus
def test_t2_retrieval_feeds_locator_and_editor_and_guard_allows_corpus(tmp_path):
    conf = cfg.from_dict({"retrieval": True, "retriever": "bm25", "retrieval_k": 5, "edit_mode": "free_json"})
    pred, log, seen = run_t2(tmp_path, T2, conf)
    assert pred["error"] is None and log["violations"] == []
    assert str((CORPUS / "regs.jsonl").resolve()) in log["files_read"]
    st = log["stages"]["retrieval"]
    assert st["calls"] >= 1 and st["chunks"] >= 1 and "첨부3-DC-02-예외" in st["clause_ids"]
    editor_msgs = [m for n, m in seen if n == "editor"]
    assert editor_msgs and "첨부3-DC-02-예외" in editor_msgs[0][1]["content"]


@need_corpus
def test_unknown_attribute_falls_back_to_llm_with_evidence(tmp_path):
    conf = cfg.from_dict({"retrieval": True, "retriever": "bm25", "retrieval_k": 5, "edit_mode": "free_json"})
    pred, log, seen = run_t2(tmp_path, T2_UNKNOWN, conf, judge=lambda l: False)
    loc = [m for n, m in seen if n == "locator"]
    assert loc and "관련 규정 근거" in loc[0][-1]["content"]
    assert log["locator"]["rule_info"]["fallback"] == "no_rule_for_attr"


@need_corpus
def test_t3_add_retrieval_in_link_judgment(tmp_path):
    inp = build_case(tmp_path / "case" / "input", request=ADD)
    seen = []

    def fn(messages, name):
        seen.append((name, messages))
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": "IA-05" in l, "reason": "t"} for i, l in enumerate(lines)]}
        return {"edits": []}
    conf = cfg.from_dict({"retrieval": True, "retriever": "bm25", "retrieval_k": 5, "edit_mode": "free_json"})
    pred, log = run_case(inp, conf, llm=MockLLM(fn), work_dir=tmp_path / "w")
    loc = [m for n, m in seen if n == "locator"][0][-1]["content"]
    assert "관련 규정 근거" in loc and "첨부3-" in loc


@need_corpus
def test_v4_issue_carries_clause_id(tmp_path):
    from document_ai.cascade.apply import apply_edits
    from document_ai.cascade.cells import parse_case
    from document_ai.cascade.graph import CellGraph
    from document_ai.cascade.locator import locate
    from document_ai.cascade.request import parse_request
    from document_ai.cascade.trace_log import TraceLog
    from document_ai.cascade.verifier import verify
    inp = build_case(tmp_path / "case" / "input", request=T2)
    g = CellGraph(parse_case(inp))
    ch = parse_request(T2)
    res = locate(ch, g, cfg.from_dict({"relevance": False}), None, TraceLog("t"))
    ar = apply_edits(inp, tmp_path / "o", [])
    issues = verify(ch, res, [], g, parse_case(tmp_path / "o"), cfg.CascadeConfig(), ar)
    v4 = [i for i in issues if i["type"] == "V4"]
    assert v4 and all("첨부3-DC-02-예외" in i["detail"] for i in v4)
