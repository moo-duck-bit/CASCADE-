"""G2: 자연어 요청 → 변경 객체(LLM 1회), 범용 전파 유형, 필수 필드 누락 시 명확한 실패, 구조화 요청 경로 유지."""

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.intent import IntentError, parse_natural
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.request import PROPAGATION, parse_request
from document_ai.cascade.run import run_case
from document_ai.cascade.trace_log import TraceLog

from .fixture_docs import build_case
from .test_m8_loop_gate import GOOD, T1, scripted

EMPTY = {"origin_item": "", "target": "", "old": "", "new": "", "attr": "", "component": "", "title": "",
         "content": "", "item": "", "reason": "t"}


def one(**kw):
    """intent_v2 출력 형식: 변경 객체 목록."""
    return {"changes": [{**EMPTY, **kw}]}


def graph(tmp_path):
    inp = build_case(tmp_path / "c" / "input")
    return inp, CellGraph(parse_case(inp))


def test_fact(tmp_path):
    _, g = graph(tmp_path)
    llm = MockLLM([one(type="fact", origin_item="Req.2", target="계정 잠금 시간", old="15분", new="30분")])
    ch = parse_natural("계정 잠금 시간을 30분으로 바꿔 주세요.", g, llm, TraceLog("t"))
    assert (ch.kind, ch.origin_req, ch.old, ch.new, ch.title) == ("T1", "Req. 2", "15분", "30분", "계정 잠금")
    assert PROPAGATION[ch.kind] == "fact" and ch.text == "계정 잠금 시간을 30분으로 바꿔 주세요."
    user = llm.calls[0]["messages"][1]["content"]
    assert "Req. 2 '계정 잠금'" in user and "stores_pii" in user


def test_constraint_add_delete(tmp_path):
    _, g = graph(tmp_path)
    ch = parse_natural("개인정보를 저장하게 됐어요", g, MockLLM([one(type="constraint", attr="stores_pii",
                                                                     old="false", new="true")]), TraceLog("t"))
    assert (ch.kind, ch.attr, ch.old, ch.new) == ("T2", "stores_pii", False, True) and ch.attr_ko == "개인식별정보 저장"
    ch = parse_natural("앱에 비밀번호 재설정 기능을 넣어 주세요", g, MockLLM([one(type="structural_add",
                       component="모바일 애플리케이션", title="비밀번호 재설정", content="이메일 인증 후 재설정한다.")]),
                       TraceLog("t"))
    assert ch.kind == "T3_add" and ch.new_req == "Req. 4"  # 같은 구성요소 마지막 번호 다음
    ch = parse_natural("서버 동기화는 빼 주세요", g, MockLLM([one(type="structural_delete", item="Req. 100")]),
                       TraceLog("t"))
    assert (ch.kind, ch.req, ch.title) == ("T3_delete", "Req. 100", "데이터 동기화")


@pytest.mark.parametrize("out,msg", [
    ({**EMPTY, "type": "fact", "origin_item": "Req. 2", "target": "잠금", "old": "", "new": "30분"}, "old"),
    ({**EMPTY, "type": "fact", "origin_item": "Req. 9", "target": "잠금", "old": "15분", "new": "30분"}, "Req. 9"),
    ({**EMPTY, "type": "constraint", "attr": "unknown_attr", "old": "false", "new": "true"}, "attr"),
    ({**EMPTY, "type": "structural_delete", "item": ""}, "item"),
])
def test_missing_fields_fail_clearly(tmp_path, out, msg):
    _, g = graph(tmp_path)
    with pytest.raises(IntentError) as e:
        parse_natural("요청", g, MockLLM([{"changes": [out]}]), TraceLog("t"))
    assert msg in str(e.value)


def test_natural_mode_run_reads_nl_and_logs_intent(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=T1)
    (inp / "request_nl.txt").write_text("계정 잠금 시간을 30분으로 바꿔 주세요.", encoding="utf-8")
    intent = {**EMPTY, "type": "fact", "origin_item": "Req. 2", "target": "계정 잠금 시간", "old": "15분", "new": "30분"}
    fn = scripted([GOOD])
    calls = []

    def wrap(m, n):
        calls.append(n)
        return {"changes": [intent]} if n == "intent" else fn(m, n)
    conf = cfg.from_dict({"request_mode": "natural", "edit_mode": "free_json",
                          "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}})
    pred, log = run_case(inp, conf, llm=MockLLM(wrap), work_dir=tmp_path / "w")
    assert calls[0] == "intent" and pred["error"] is None
    assert log["intent"]["origin_req"] == "Req. 2" and pred["propagation_type"] == "fact"
    assert str((inp / "request_nl.txt").resolve()) in log["files_read"]


def test_natural_mode_failure_recorded(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=T1)
    (inp / "request_nl.txt").write_text("뭔가 바꿔 주세요.", encoding="utf-8")
    conf = cfg.from_dict({"request_mode": "natural"})
    pred, log = run_case(inp, conf, llm=MockLLM([one(type="fact")]), work_dir=tmp_path / "w")
    assert pred["parse_ok"] is False and pred["error"].startswith("IntentError") and pred["edits"] == []


def test_structured_mode_keeps_parser_path(tmp_path):
    inp = build_case(tmp_path / "c" / "input", request=T1)
    conf = cfg.from_dict({"edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}})
    llm = MockLLM(scripted([GOOD]))
    pred, log = run_case(inp, conf, llm=llm, work_dir=tmp_path / "w")
    assert "intent" not in [c["name"] for c in llm.calls] and pred["propagation_type"] == "fact"
    assert cfg.CascadeConfig().request_mode == "structured"
    with pytest.raises(ValueError):
        cfg.from_dict({"request_mode": "magic"})
    assert parse_request(T1).kind == "T1"
