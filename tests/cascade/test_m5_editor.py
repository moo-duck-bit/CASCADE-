import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.cells import parse_case
from document_ai.cascade.editor import edit, list_add, list_remove, revise
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import locate
from document_ai.cascade.postfix import dedupe_units, fix_particles, postfix
from document_ai.cascade.request import parse_request
from document_ai.cascade.trace_log import TraceLog

from .fixture_docs import build_case


# 후처리

@pytest.mark.parametrize("src,dst", [
    ("5회 회 실패 시", "5회 실패 시"), ("10분 분 동안", "10분 동안"), ("최대 3번 회까지", "최대 3번까지"),
    ("30 분 분", "30 분"), ("10 분 동안 15분", "10 분 동안 15분"),
])
def test_dedupe_units(src, dst):
    assert dedupe_units(src) == dst


@pytest.mark.parametrize("src,dst", [
    ("10분를 넘으면", "10분을 넘으면"), ("3회을 초과", "3회를 초과"), ("12시간이 지나면", "12시간이 지나면"),
    ("TLS 1.3를 사용", "TLS 1.3을 사용"), ("10자으로 설정", "10자로 설정"), ("365일로 변경", "365일로 변경"),
    ("30분와 같이", "30분과 같이"), ("4회는 고정", "4회는 고정"), ("4회은 고정", "4회는 고정"),
    ("사이의 값을", "사이의 값을"),  # 값 뒤가 아니면 건드리지 않는다
])
def test_fix_particles(src, dst):
    assert fix_particles(src) == dst


def test_postfix_joins_lines():
    assert postfix("첫 줄\n둘째 줄 10분를") == "첫 줄 둘째 줄 10분을"


# 목록 편집

def test_list_add_remove():
    assert list_add("Req. 2, Req. 101", "Req. 15") == "Req. 2, Req. 15, Req. 101"
    assert list_add("해당 없음", "Req. 3") == "Req. 3"
    assert list_add("Req. 1, Req. 3", "Req. 3") == "Req. 1, Req. 3"
    assert list_remove("Req. 2, Req. 101, Req. 104", "Req. 101") == "Req. 2, Req. 104"
    assert list_remove("Req. 2", "Req. 2") == "-"


# Editor

@pytest.fixture()
def graph(tmp_path):
    return CellGraph(parse_case(build_case(tmp_path / "in")))


def test_t1_editor_mock_output_to_edits(graph):
    ch = parse_request("Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다.")
    res = locate(ch, graph, cfg.preset("no_relevance", edit_mode="free_json"), MockLLM([]), TraceLog("t"))
    targets = [c.site for c in res.relevant]
    llm = MockLLM([{"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분를 넘지 않게 30분 분 차단되어야 한다."},
                              {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 삼십 분 동안 잠근다."}]}])
    log = TraceLog("t")
    edits = edit(ch, res, graph, cfg.from_dict({"edit_mode": "free_json"}), llm, log)
    by = {e["site"]: e for e in edits}
    assert by["[MDSR | Req. 2 | 설명]"]["new_text"] == "잠긴 계정은 30분을 넘지 않게 30분 차단되어야 한다."
    assert by["[MDSR | Req. 2 | 설명]"]["op"] == "modify"
    assert (by["[MDSR | Req. 2 | 설명]"]["doc"], by["[MDSR | Req. 2 | 설명]"]["item"],
            by["[MDSR | Req. 2 | 설명]"]["field"]) == ("MDSR", "Req. 2", "설명")
    prompt = llm.calls[0]["messages"][-1]["content"]
    assert all(t in prompt for t in targets)
    assert "[MDSR | Req. 2 | 목적]" in prompt  # 같은 항목의 다른 칸은 맥락으로
    assert log.n_calls == 1


def test_t3_delete_is_structural_no_llm(graph):
    ch = parse_request("Req. 2 '계정 잠금' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다.")
    res = locate(ch, graph, cfg.from_dict({"edit_mode": "free_json"}), MockLLM([]), TraceLog("t"))
    llm = MockLLM([])
    edits = edit(ch, res, graph, cfg.from_dict({"edit_mode": "free_json"}), llm, TraceLog("t"))
    by = {e["site"]: e for e in edits}
    assert by["[MDSR | Req. 2 | 설명]"]["op"] == "mark_deleted" and by["[MDSR | Req. 2 | 설명]"]["new_text"] == "삭제"
    assert by["[MDSR | IA-07 | 소프트웨어 기능 요구사항 번호]"]["new_text"] == "Req. 1"
    assert by["[XXCS | IA-07 | 해당 요구사항]"]["new_text"] == "Req. 1"
    assert not llm.calls


def test_t2_to_applied_mixes_rule_and_llm(graph):
    ch = parse_request("제품 사이버보안 속성 '개인식별정보 저장'(stores_pii)을 false에서 true로 변경한다.")
    res = locate(ch, graph, cfg.preset("no_relevance", edit_mode="free_json"), MockLLM([]), TraceLog("t"))
    llm = MockLLM([{"edits": [{"site": "[XXCS | DC-02 | 체크리스트 입증 방법]", "new_text": "비식별화 처리 기준 확인"},
                              {"site": "[XXCS | DC-02 | 판정]", "new_text": "시험 예정"}]}])
    edits = edit(ch, res, graph, cfg.from_dict({"edit_mode": "free_json"}), llm, TraceLog("t"))
    by = {e["site"]: e for e in edits}
    assert by["[XXCS | DC-02 | 적용 여부]"]["new_text"] == "적용" and by["[XXCS | DC-02 | 적용 여부]"]["source"] == "rule"
    assert by["[XXCS | DC-02 | 체크리스트 적용 여부]"]["new_text"] == "적용"
    assert by["[XXCS | DC-02 | 체크리스트 입증 방법]"]["source"] == "llm"


def test_t2_to_na_is_structural(graph):
    ch = parse_request("제품 사이버보안 속성 '로컬·네트워크 접속 기능'(remote_access)을 true에서 false로 변경한다.")
    res = locate(ch, graph, cfg.from_dict({"edit_mode": "free_json"}), MockLLM(lambda m, n: {"judgments": []}), TraceLog("t"))
    llm = MockLLM([])
    edits = edit(ch, res, graph, cfg.from_dict({"edit_mode": "free_json"}), llm, TraceLog("t"))
    by = {e["site"]: e["new_text"] for e in edits}
    assert by == {"[XXCS | UC-03 | 체크리스트 적용 여부]": "N/A", "[XXCS | UC-03 | 적용 여부]": "N/A",
                  "[XXCS | UC-03 | 체크리스트 입증 방법]": "해당 없음", "[XXCS | UC-03 | 판정]": "해당 없음"}
    assert not llm.calls


def test_t3_add_inserts_and_link_append(graph):
    ch = parse_request("모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 본인 인증 후 재설정한다.")
    loc_llm = MockLLM(lambda m, n: {"judgments": [{"id": i, "relevant": "IA-05" in ln, "reason": "t"}
                                                   for i, ln in enumerate(l for l in m[-1]["content"].splitlines()
                                                                          if l.startswith("- id="))]})
    res = locate(ch, graph, cfg.from_dict({"edit_mode": "free_json"}), loc_llm, TraceLog("t"))
    ins = [c.site for c in res.relevant if c.kind == "insert"]
    llm = MockLLM([{"edits": [{"site": s, "new_text": f"내용 {i}"} for i, s in enumerate(ins)]}])
    edits = edit(ch, res, graph, cfg.from_dict({"edit_mode": "free_json"}), llm, TraceLog("t"))
    by = {e["site"]: e for e in edits}
    assert by["[MDSR | Req. 4 | 번호]"]["new_text"] == "Req. 4" and by["[MDSR | Req. 4 | 번호]"]["op"] == "insert"
    assert by["[MDSR | Req. 4 | 제목]"]["new_text"] == "비밀번호 재설정"
    assert by["[MDSR | Req. 4 | 설명]"]["op"] == "insert" and by["[MDSR | Req. 4 | 설명]"]["anchor"] == "Req. 3"
    assert by["[MDSR | IA-05 | 소프트웨어 기능 요구사항 번호]"]["new_text"] == "Req. 1, Req. 4"
    assert "[MDSR | Req. 3 | 설명]" in llm.calls[0]["messages"][-1]["content"]  # 앵커 Req를 문체 예시로


def test_revise_changes_only_flagged(graph):
    ch = parse_request("Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다.")
    res = locate(ch, graph, cfg.preset("no_relevance", edit_mode="free_json"), MockLLM([]), TraceLog("t"))
    prev = [{"site": "[MDSR | Req. 2 | 설명]", "doc": "MDSR", "item": "Req. 2", "field": "설명", "op": "modify",
             "new_text": "잠긴 계정은 30분 동안 차단되어야 한다.", "source": "llm", "kind": "origin"},
            {"site": "[MDSR | Req. 2 | 기준#p1]", "doc": "MDSR", "item": "Req. 2", "field": "기준#p1", "op": "modify",
             "new_text": "15 분 동안 접근 차단을 확인함", "source": "llm", "kind": "origin"}]
    issues = [{"type": "V2", "site": "[MDSR | Req. 2 | 기준#p1]", "detail": "옛 값 15분이 남아 있음"}]
    llm = MockLLM([{"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "몰래 바꾼 글"},
                              {"site": "[MDSR | Req. 2 | 기준#p1]", "new_text": "30 분 동안 접근 차단을 확인함"}]}])
    edits = revise(ch, res, graph, cfg.from_dict({"edit_mode": "free_json"}), llm, TraceLog("t"), prev, issues)
    by = {e["site"]: e["new_text"] for e in edits}
    assert by["[MDSR | Req. 2 | 설명]"] == "잠긴 계정은 30분 동안 차단되어야 한다."  # 지적 안 된 칸은 유지
    assert by["[MDSR | Req. 2 | 기준#p1]"] == "30 분 동안 접근 차단을 확인함"
    msg = llm.calls[0]["messages"][-1]["content"]
    assert "아래 지적은 문제일 수도 아닐 수도 있다" in msg and "V2" in msg
