import json

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import locate
from document_ai.cascade.request import parse_request
from document_ai.cascade.trace_log import TraceLog

from .fixture_docs import build_case


@pytest.fixture()
def graph(tmp_path):
    return CellGraph(parse_case(build_case(tmp_path / "in")))


def judge_all(relevant=True, keep=None):
    """후보를 모두(또는 keep 사이트만) 관련으로 판정하는 MockLLM 응답 함수."""
    def fn(messages, name):
        user = messages[-1]["content"]
        out = []
        for line in user.splitlines():
            if line.startswith("- id="):
                cid = int(line.split()[1].split("=")[1])
                rel = relevant if keep is None else any(k in line for k in keep)
                out.append({"id": cid, "relevant": rel, "reason": "test"})
        return {"judgments": out}
    return fn


def sites(cands):
    return {c.site for c in cands}


def test_t1_candidates_by_kind(graph):
    ch = parse_request("Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다.")
    res = locate(ch, graph, cfg.CascadeConfig(), MockLLM(judge_all()), TraceLog("t"))
    kinds = {c.site: c.kind for c in res.candidates}
    assert kinds["[MDSR | Req. 2 | 설명]"] == "origin"
    assert kinds["[MDDR | Req. 2 | 설계#p1]"] == "same_id"  # 십오 분
    assert kinds["[XXCS | IA-07 | 시험방법#p2]"] == "trace"  # Req. 2 ↔ IA-07
    assert kinds["[MDSR | 3.4 일반적 제한 | 본문#p1]"] == "value"
    assert kinds["[XXCS | UC-03 | 시험방법]"] == "value"  # UC-03은 Req. 3 연결이라 trace가 아님
    assert "[MDSR | Req. 1 | 설명]" not in kinds  # 15분 없음
    assert sites(res.relevant) == sites(res.candidates)


def test_t1_relevance_filters_and_prompt_format(graph):
    ch = parse_request("Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다.")
    llm = MockLLM(judge_all(keep=["Req. 2 | 설명", "Req. 2 | 기준#p1", "IA-07"]))
    res = locate(ch, graph, cfg.CascadeConfig(), llm, TraceLog("t"))
    assert "[MDSR | Req. 2 | 기준#p2]" not in sites(res.relevant)  # 해제 안내 15분은 다른 사실
    assert "[MDSR | Req. 2 | 설명]" in sites(res.relevant)
    assert len(llm.calls) == 1 and llm.calls[0]["name"] == "locator"
    prompt = llm.calls[0]["messages"][-1]["content"]
    assert "같은 사실" in prompt and "시험 조건이나 결과로 다루는가" in prompt
    assert all(j["reason"] for j in res.judgments)


def test_relevance_off_keeps_all_and_no_llm(graph):
    ch = parse_request("Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다.")
    llm = MockLLM([])
    res = locate(ch, graph, cfg.preset("no_relevance"), llm, TraceLog("t"))
    assert sites(res.relevant) == sites(res.candidates) and not llm.calls


def test_value_index_off_uses_literal_surface(graph):
    ch = parse_request("Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다.")
    res = locate(ch, graph, cfg.preset("no_value_index", relevance=False), MockLLM([]), TraceLog("t"))
    s = sites(res.candidates)
    assert "[MDSR | Req. 2 | 설명]" in s and "[MDDR | Req. 2 | 설계#p1]" not in s  # 십오 분은 못 찾음


def test_t2_rule_candidates(graph):
    ch = parse_request("제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다.")
    res = locate(ch, graph, cfg.CascadeConfig(), MockLLM(judge_all(relevant=False)), TraceLog("t"))
    rule = {c.site for c in res.relevant if c.kind == "rule"}
    assert rule == {"[XXCS | DC-02 | 체크리스트 적용 여부]", "[XXCS | DC-02 | 체크리스트 입증 방법]",
                    "[XXCS | DC-02 | 적용 여부]", "[XXCS | DC-02 | 판정]"}
    assert res.rule_info["flips"] == [{"sec": "DC-02", "before": "N/A", "after": "적용", "rule": "R09"}]
    links = {c.site for c in res.candidates if c.kind == "rule_link"}
    assert "[MDSR | DC-02 | 소프트웨어 기능 요구사항 번호]" in links


def test_t2_rules_off_llm_picks_secs(graph):
    ch = parse_request("제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다.")
    llm = MockLLM(judge_all(keep=["DC-02"]))
    res = locate(ch, graph, cfg.preset("no_rules"), llm, TraceLog("t"))
    assert {c.site for c in res.relevant if c.kind == "rule"} >= {"[XXCS | DC-02 | 적용 여부]"}
    assert all("DC-02" in c.site for c in res.relevant)
    assert "적용 예외" in llm.calls[0]["messages"][-1]["content"]


def test_t3_add_inserts_and_links(graph):
    ch = parse_request("모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 본인 인증 후 재설정한다.")
    res = locate(ch, graph, cfg.CascadeConfig(), MockLLM(judge_all(keep=["IA-05"])), TraceLog("t"))
    ins = [c.site for c in res.candidates if c.kind == "insert"]
    assert ins[:4] == ["[MDSR | Req. 4 | 번호]", "[MDSR | Req. 4 | 제목]", "[MDSR | Req. 4 | 설명]",
                       "[MDSR | Req. 4 | 목적]"]
    assert "[MDSR | Req. 4 | 기준#p1]" in ins and "[MDDR | Req. 4 | 번호]" in ins
    assert res.anchors["MDSR"] == "Req. 3" and res.anchors["MDDR"] == "Req. 3"
    link = {c.site for c in res.relevant if c.kind == "link"}
    assert link == {"[MDSR | IA-05 | 소프트웨어 기능 요구사항 번호]", "[XXCS | IA-05 | 해당 요구사항]"}
    assert all(not c.site.startswith("[XXCS | DC-02") for c in res.candidates)  # N/A 항목은 후보에서 뺀다


def test_t3_add_server_anchor(graph):
    ch = parse_request("서버 요구사항 Req. 101 '로그 반출 차단'을(를) 신규 추가한다. 내용: 반출을 막는다.")
    res = locate(ch, graph, cfg.preset("no_relevance"), MockLLM([]), TraceLog("t"))
    assert res.anchors["MDSR"] == "Req. 100"


def test_t3_delete_refs_without_llm(graph):
    ch = parse_request("Req. 2 '계정 잠금' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다.")
    llm = MockLLM([])
    res = locate(ch, graph, cfg.CascadeConfig(), llm, TraceLog("t"))
    kinds = {c.site: c.kind for c in res.relevant}
    assert kinds["[MDSR | Req. 2 | 설명]"] == "delete"
    assert "[MDSR | Req. 2 | 번호]" not in kinds
    assert kinds["[MDDR | Req. 2 | 설계#p1]"] == "delete"
    assert kinds["[MDSR | IA-07 | 소프트웨어 기능 요구사항 번호]"] == "unlink"
    assert kinds["[XXCS | IA-07 | 해당 요구사항]"] == "unlink"
    assert "[MDSR | Req. 3 | 번호]" not in kinds and not llm.calls


def test_ledger_like_expands_one_hop(graph):
    ch = parse_request("Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다.")
    res = locate(ch, graph, cfg.preset("ledger_like"), MockLLM([]), TraceLog("t"))
    s = sites(res.relevant)
    assert "[MDDR | Req. 2 | 설계#p1]" in s and "[XXCS | IA-07 | 시험방법#p2]" in s
    assert "[XXCS | UC-03 | 시험방법]" not in s  # 값 색인을 쓰지 않는다
