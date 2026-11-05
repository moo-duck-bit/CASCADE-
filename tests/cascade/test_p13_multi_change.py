"""P13: 변경이 여러 개인 요청(v0.3). 속성 여러 개는 T2 변경 하나, '… 또한 …' 복합 요청은 변경마다 따로 돌려 합친다."""

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade import rules as rules_mod
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.intent import IntentError, to_changes
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import change_info, locate
from document_ai.cascade.request import (RequestError, attr_changes, current_attrs, parse_request,
                                         parse_requests)
from document_ai.cascade.run import merge_parts, retrieval_queries, retrieve, run_case
from document_ai.cascade.trace_log import TraceLog

from .fixture_docs import build_case
from .test_g2_intent import EMPTY
from .test_m8_loop_gate import GOOD

T2_ONE = "제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다. 첨부3 적용 예외에 따라 문서를 갱신한다."
T2_PAIR = ("제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로, '아이디·비밀번호 인증 사용'(password_auth)을 "
           "true에서 false로 변경한다. 첨부3 적용 예외에 따라 문서를 갱신한다.")
ADD = "모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 본인 인증 후 비밀번호를 재설정한다."
T1 = "Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다."
NOCHECK = {"V2b": False, "V3b": False, "V6b": False}


@pytest.fixture()
def graph(tmp_path):
    return CellGraph(parse_case(build_case(tmp_path / "g" / "input")))


# 요청 읽기

def test_single_requests_unchanged():
    for text in (T2_ONE, ADD, T1, "Req. 2 '계정 잠금' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다."):
        chs = parse_requests(text)
        assert len(chs) == 1 and chs[0] == parse_request(text) and chs[0].attr_changes == []
    r = parse_request(T2_ONE)
    assert attr_changes(r) == [("stores_pii", "개인식별정보 저장", False, True)]


def test_t2_pair_is_one_change():
    r = parse_request(T2_PAIR)
    assert (r.kind, r.attr, r.old, r.new) == ("T2", "stores_pii", False, True)
    assert attr_changes(r) == [("stores_pii", "개인식별정보 저장", False, True),
                               ("password_auth", "아이디·비밀번호 인증 사용", True, False)]


def test_compound_add_with_value_change():
    chs = parse_requests(f"{ADD} 또한 {T1}")
    assert [c.kind for c in chs] == ["T3_add", "T1"]
    assert chs[0].new_req == "Req. 4" and chs[0].content == "본인 인증 후 비밀번호를 재설정한다."
    assert (chs[1].origin_req, chs[1].old, chs[1].new) == ("Req. 2", "15분", "30분")
    with pytest.raises(RequestError) as e:
        parse_request(f"{ADD} 또한 {T1}")
    assert "parse_requests" in str(e.value)


def test_compound_add_with_attr_pair():
    chs = parse_requests(f"{ADD} 또한 {T2_PAIR}")
    assert [c.kind for c in chs] == ["T3_add", "T2"]
    assert "속성" not in chs[0].content and len(attr_changes(chs[1])) == 2


def test_also_inside_content_is_not_a_split():
    text = ("모바일 애플리케이션 요구사항 Req. 4 '기록 내보내기'을(를) 신규 추가한다. "
            "내용: 기록을 파일로 내보낸다. 또한 내보낸 파일은 암호화하여야 한다.")
    chs = parse_requests(text)
    assert len(chs) == 1 and chs[0].content == "기록을 파일로 내보낸다. 또한 내보낸 파일은 암호화하여야 한다."
    chs = parse_requests(f"{text} 또한 {T1}")
    assert [c.kind for c in chs] == ["T3_add", "T1"] and chs[0].content.endswith("암호화하여야 한다.")


# 규칙 계산·위치 찾기·검사

def test_flips_many(graph):
    table, attrs = graph.rule_table, {"stores_pii": False, "password_auth": True}
    one = rules_mod.flips("stores_pii", False, True, attrs, table)
    assert rules_mod.flips_many([("stores_pii", False, True)], attrs, table) == one
    both = rules_mod.flips_many([("stores_pii", False, True), ("password_auth", True, False)], attrs, table)
    assert [(f["sec"], f["before"], f["after"]) for f in both] == [("DC-02", "N/A", "적용"), ("IA-05", "적용", "N/A")]


def test_t2_pair_attrs_and_candidates(graph):
    ch = parse_request(T2_PAIR)
    attrs, notes = current_attrs(graph, ch)
    assert attrs["stores_pii"] is False and attrs["password_auth"] is True and notes == []
    llm = MockLLM(lambda m, n: {"judgments": [{"id": i, "relevant": False, "reason": "t"}
                                              for i, l in enumerate(m[-1]["content"].splitlines()) if l.startswith("- id=")]})
    res = locate(ch, graph, cfg.CascadeConfig(), llm, TraceLog("t"))
    rule = {c.site for c in res.relevant if c.kind == "rule"}
    assert {"[XXCS | DC-02 | 적용 여부]", "[XXCS | IA-05 | 적용 여부]", "[XXCS | IA-05 | 판정]"} <= rule
    assert res.rule_info["attrs"] == ["stores_pii", "password_auth"]
    info = change_info(ch)
    assert "사이버보안 속성 1: stores_pii" in info and "사이버보안 속성 2: password_auth" in info
    assert change_info(parse_request(T2_ONE)) == "사이버보안 속성: stores_pii(개인식별정보 저장)\n옛 값: false\n새 값: true"


def test_retrieval_queries_per_attr():
    assert retrieval_queries(parse_request(T2_ONE)) == ["개인식별정보 저장 stores_pii 적용 예외사항 적용하지 않는다"]
    qs = retrieval_queries(parse_request(T2_PAIR))
    assert len(qs) == 2 and qs[1].startswith("아이디·비밀번호 인증 사용 password_auth")

    class Fake:
        name, calls = "fake", 0

        def search(self, q, k):
            self.calls += 1
            return [{"clause_id": "C-shared"}, {"clause_id": f"C-{q[:4]}"}]
    log = TraceLog("t")
    hits = retrieve(parse_request(T2_PAIR), cfg.CascadeConfig(), Fake(), log)
    assert [h["clause_id"] for h in hits] == ["C-shared", "C-개인식별", "C-아이디·"]
    assert log.stages["retrieval"]["calls"] == 2 and " | " in log.stages["retrieval"]["query"]


def test_merge_parts_keeps_first_on_conflict():
    a = [{"site": "[X | A | f]", "new_text": "1"}, {"site": "[X | B | f]", "new_text": "2"}]
    b = [{"site": "[X | B | f]", "new_text": "3"}, {"site": "[X | C | f]", "new_text": "4"}]
    merged, conflicts = merge_parts([a, b])
    assert [e["new_text"] for e in merged] == ["1", "2", "4"]
    assert conflicts == [{"site": "[X | B | f]", "kept_part": 0, "dropped_part": 1}]


# 실행

ADD_EDITS = {"edits": [
    {"site": "[MDSR | Req. 4 | 설명]", "new_text": "사용자는 본인 인증 후 비밀번호를 재설정할 수 있어야 한다."},
    {"site": "[MDSR | Req. 4 | 목적]", "new_text": "비밀번호 분실 시 안전하게 계정을 되찾게 한다."},
    {"site": "[MDSR | Req. 4 | 기준#p1]", "new_text": "본인 인증 없이 재설정이 거부됨을 확인함"},
    {"site": "[MDSR | Req. 4 | 기준#p2]", "new_text": "재설정 후 새 비밀번호로 로그인됨을 확인함"},
    {"site": "[MDDR | Req. 4 | 설계#p1]", "new_text": "• 본인 인증을 먼저 요구한다."},
    {"site": "[MDDR | Req. 4 | 설계#p2]", "new_text": "• 인증 후 새 비밀번호를 받는다."}]}


def responder(keep, editor):
    def fn(messages, name):
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": any(k in l for k in keep), "reason": "t"}
                                  for i, l in enumerate(lines)]}
        return editor(messages[1]["content"])
    return fn


def test_run_compound_add_and_value(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=f"{ADD} 또한 {T1}")
    llm = MockLLM(responder(["IA-05", "Req. 2 | 설명", "MDDR | Req. 2 | 설계#p1"],
                            lambda user: ADD_EDITS if "[MDSR | Req. 4 | 설명]" in user else GOOD))
    conf = cfg.from_dict({"edit_mode": "free_json", "verifier_checks": NOCHECK, "retrieval": False})
    pred, log = run_case(inp, conf, llm=llm, work_dir=tmp_path / "w", case_id="T3_x")
    assert pred["error"] is None and pred["changes"] == ["T3_add", "T1"]
    assert pred["case_type"] == "T3" and pred["propagation_type"] == "structural_add+fact"
    by = {e["site"]: e["new_text"] for e in pred["edits"]}
    assert by["[MDSR | Req. 4 | 제목]"] == "비밀번호 재설정"
    assert by["[MDSR | IA-05 | 소프트웨어 기능 요구사항 번호]"] == "Req. 1, Req. 4"
    assert by["[MDSR | Req. 2 | 설명]"].startswith("잠긴 계정은 30분")
    assert [p["kind"] for p in log["parts"]] == ["T3_add", "T1"] and log["merge"]["conflicts"] == []
    assert {r["part"] for r in log["rounds"]} == {0, 1}
    assert log["stages"]["locator"]["candidates"] == sum(p["stages"]["locator"]["candidates"] for p in log["parts"])
    assert (tmp_path / "w" / "part0" / "round0").is_dir() and (tmp_path / "w" / "part1" / "round0").is_dir()
    # 마지막 검사(전체 검사 켬)에서 추가 쪽 지적은 없다. T1 쪽 V2b·V6b는 픽스처에 옛 값 칸이 남아서다(단일 요청과 같음).
    assert not {"V1", "V3", "V3b", "V5"} & set(log["gate"]["final_issues"])


def test_run_t2_pair_one_pass(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=T2_PAIR)
    out = {"edits": [{"site": "[XXCS | DC-02 | 체크리스트 입증 방법]", "new_text": "개인식별정보 비식별화 처리를 확인한다."},
                     {"site": "[XXCS | DC-02 | 판정]", "new_text": "시험 예정"}]}
    llm = MockLLM(responder([], lambda user: out))
    conf = cfg.from_dict({"edit_mode": "free_json", "verifier_checks": NOCHECK, "retrieval": False})
    pred, log = run_case(inp, conf, llm=llm, work_dir=tmp_path / "w", case_id="T2_x")
    assert pred["error"] is None and "changes" not in pred and pred["propagation_type"] == "constraint"
    by = {e["site"]: e["new_text"] for e in pred["edits"]}
    assert by["[XXCS | DC-02 | 적용 여부]"] == "적용" and by["[XXCS | IA-05 | 적용 여부]"] == "N/A"
    assert by["[XXCS | IA-05 | 판정]"] == "해당 없음" and by["[XXCS | DC-02 | 판정]"] == "시험 예정"
    assert "V4" not in log["gate"]["final_issues"] and "parts" not in log
    assert [c["name"] for c in llm.calls].count("editor") == 1


# 자연어

def test_natural_merges_constraints_and_numbers(graph):
    out = {"changes": [{**EMPTY, "type": "structural_add", "component": "모바일 애플리케이션", "title": "가", "content": "가"},
                       {**EMPTY, "type": "constraint", "attr": "stores_pii", "old": "false", "new": "true"},
                       {**EMPTY, "type": "constraint", "attr": "password_auth", "old": "true", "new": "false"},
                       {**EMPTY, "type": "structural_add", "component": "모바일 애플리케이션", "title": "나", "content": "나"}]}
    chs = to_changes(out, "요청", graph)
    assert [c.kind for c in chs] == ["T3_add", "T2", "T3_add"]
    assert [a for a, *_ in attr_changes(chs[1])] == ["stores_pii", "password_auth"]
    assert (chs[0].new_req, chs[2].new_req) == ("Req. 4", "Req. 5")
    with pytest.raises(IntentError):
        to_changes({"changes": []}, "요청", graph)
