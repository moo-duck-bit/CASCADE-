"""P3: 타입이 있는 편집 연산. 정상 적용, 잘못된 호출의 traceback 형식 오류, 오류만 고쳐 다시 내면 적용, scorer 형식 내보내기."""

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.apply import apply_edits
from document_ai.cascade.cells import parse_case
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import locate
from document_ai.cascade.ops import OPS_SCHEMA, execute, format_errors, to_scorer_edits
from document_ai.cascade.request import parse_request
from document_ai.cascade.run import PUBLIC_EDIT_KEYS, run_case
from document_ai.cascade.trace_log import TraceLog

from .fixture_docs import build_case
from .test_m8_loop_gate import T1

ADD = "모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 이메일 인증 후 비밀번호를 재설정한다."
DEL = "Req. 2 '계정 잠금' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다."


def setup(tmp_path, request):
    inp = build_case(tmp_path / "case" / "input", request=request)
    g = CellGraph(parse_case(inp))
    ch = parse_request(request)
    res = locate(ch, g, cfg.from_dict({"relevance": False}), None, TraceLog("t"))
    return inp, g, ch, res


def texts(inp, out, edits):
    apply_edits(inp, out, edits)
    return {c.site: c.text for c in parse_case(out)}


def test_set_cell_applies(tmp_path):
    inp, g, ch, res = setup(tmp_path, T1)
    edits, errors = execute([{"op": "set_cell", "site": "[MDSR | Req. 2 | 설명]", "text": "30분 동안 차단"}], ch, res, g)
    assert errors == [] and edits[0]["op"] == "modify" and edits[0]["source"] == "llm"
    assert texts(inp, tmp_path / "o", edits)["[MDSR | Req. 2 | 설명]"] == "30분 동안 차단"


def test_insert_block_applies(tmp_path):
    inp, g, ch, res = setup(tmp_path, ADD)
    call = {"op": "insert_block", "doc": "MDSR", "anchor": "Req. 3", "block_type": "req",
            "fields": [{"name": "설명", "text": "새 설명"}, {"name": "목적", "text": "새 목적"},
                       {"name": "기준#p1", "text": "새 기준 1"}, {"name": "기준#p2", "text": "새 기준 2"}]}
    edits, errors = execute([call], ch, res, g)
    assert errors == [] and {e["op"] for e in edits} == {"insert"}
    assert all(e["anchor"] == "Req. 3" and e["item"] == "Req. 4" for e in edits)
    after = texts(inp, tmp_path / "o", edits + [{"site": "[MDSR | Req. 4 | 번호]", "doc": "MDSR", "item": "Req. 4",
                                                "field": "번호", "op": "insert", "new_text": "Req. 4",
                                                "anchor": "Req. 3"}])
    assert after["[MDSR | Req. 4 | 설명]"] == "새 설명"


def test_add_and_remove_link_apply(tmp_path):
    inp, g, ch, res = setup(tmp_path, ADD)
    edits, errors = execute([{"op": "add_link", "sec_id": "UC-03", "req_id": "Req. 4"}], ch, res, g)
    assert errors == []
    by = {e["site"]: e["new_text"] for e in edits}
    assert by["[MDSR | UC-03 | 소프트웨어 기능 요구사항 번호]"] == "Req. 3, Req. 4"
    assert by["[XXCS | UC-03 | 해당 요구사항]"] == "Req. 3, Req. 4"
    inp, g, ch, res = setup(tmp_path / "d", DEL)
    edits, errors = execute([{"op": "remove_link", "sec_id": "IA-07", "req_id": "Req. 2"}], ch, res, g)
    assert errors == [] and {e["new_text"] for e in edits} == {"Req. 1"}


def test_mark_deleted_applies(tmp_path):
    inp, g, ch, res = setup(tmp_path, DEL)
    edits, errors = execute([{"op": "mark_deleted", "req_id": "Req. 2"}], ch, res, g)
    assert errors == [] and {e["new_text"] for e in edits} == {"삭제"}
    sites = {e["site"] for e in edits}
    assert "[MDSR | Req. 2 | 설명]" in sites and "[MDDR | Req. 2 | 설계#p1]" in sites
    assert "[MDSR | Req. 2 | 번호]" not in sites


@pytest.mark.parametrize("call,etype", [
    ({"op": "set_cell", "site": "[MDSR | Req. 9 | 설명]", "text": "x"}, "SiteNotFound"),
    ({"op": "set_cell", "site": "[MDSR | Req. 2 | 번호]", "text": "Req. 7"}, "TargetError"),
    ({"op": "set_cell", "site": "[MDSR | IA-07 | 소프트웨어 기능 요구사항 번호]", "text": "Req. 1"}, "TargetError"),
    ({"op": "set_cell", "site": "[MDSR | Req. 2 | 설명]", "text": 30}, "ArgTypeError"),
    ({"op": "set_cell", "site": "[MDSR | Req. 2 | 설명]"}, "ArgTypeError"),
    ({"op": "insert_block", "doc": "MDSR", "anchor": "Req. 77", "block_type": "req",
      "fields": [{"name": "설명", "text": "x"}]}, "AnchorNotFound"),
    ({"op": "insert_block", "doc": "MDSR", "anchor": "Req. 3", "block_type": "table",
      "fields": [{"name": "설명", "text": "x"}]}, "ArgTypeError"),
    ({"op": "insert_block", "doc": "XXCS", "anchor": "Req. 3", "block_type": "req",
      "fields": [{"name": "설명", "text": "x"}]}, "TargetError"),
    ({"op": "add_link", "sec_id": "ZZ-01", "req_id": "Req. 1"}, "SiteNotFound"),
    ({"op": "add_link", "sec_id": "UC-03", "req_id": "Req. 55"}, "TargetError"),
    ({"op": "remove_link", "sec_id": "UC-03", "req_id": "Req. 1"}, "TargetError"),
    ({"op": "mark_deleted", "req_id": "Req. 55"}, "TargetError"),
    ({"op": "rewrite_doc", "doc": "MDSR"}, "UnknownOp"),
])
def test_bad_calls_return_traceback_errors(tmp_path, call, etype):
    inp, g, ch, res = setup(tmp_path, T1)
    edits, errors = execute([call], ch, res, g)
    assert edits == [] and len(errors) == 1
    e = errors[0]
    assert e["type"] == etype and e["op"] == call["op"] and e["message"]
    assert set(e) >= {"op", "args", "type", "message", "site", "index"}
    tb = format_errors(errors)
    assert tb.startswith("Traceback") and f"{call['op']}(" in tb and f"{etype}:" in tb


def test_good_calls_survive_next_to_bad(tmp_path):
    inp, g, ch, res = setup(tmp_path, T1)
    edits, errors = execute([{"op": "set_cell", "site": "[MDSR | Req. 9 | 설명]", "text": "x"},
                             {"op": "set_cell", "site": "[MDSR | Req. 2 | 설명]", "text": "30분"}], ch, res, g)
    assert len(edits) == 1 and len(errors) == 1 and errors[0]["index"] == 0


def ops_llm(first, second, keep=("Req. 2 | 설명", "MDDR | Req. 2 | 설계#p1")):
    outs = [first, second]
    seen = []

    def fn(messages, name):
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": any(k in l for k in keep), "reason": "t"}
                                  for i, l in enumerate(lines)]}
        seen.append(messages)
        return outs.pop(0)
    return fn, seen


GOOD_OPS = [{"op": "set_cell", "site": "[MDSR | Req. 2 | 설명]", "text": "잠긴 계정은 30분 동안 접근이 차단되어야 한다."},
            {"op": "set_cell", "site": "[MDDR | Req. 2 | 설계#p1]", "text": "• 계정을 삼십 분 동안 잠근다."}]


def test_fix_only_error_and_resubmit_applies(tmp_path):
    bad = [GOOD_OPS[0], {"op": "set_cell", "site": "[MDDR | Req. 2 | 설계#p9]", "text": "• 계정을 삼십 분 동안 잠근다."}]
    fn, seen = ops_llm({"ops": bad}, {"ops": GOOD_OPS})
    inp = build_case(tmp_path / "case" / "input", request=T1)
    pred, log = run_case(inp, cfg.from_dict({"edit_mode": "ops", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=MockLLM(fn), work_dir=tmp_path / "w")
    assert pred["error"] is None and pred["stop_reason"] == "no_issues"
    by = {e["site"]: e["new_text"] for e in pred["edits"]}
    assert by["[MDDR | Req. 2 | 설계#p1]"] == "• 계정을 삼십 분 동안 잠근다."
    assert [r["exec_errors"] for r in log["rounds"]] == [1, 0]
    fb = seen[1][-1]["content"]
    # 실행 오류와 Verifier 지적이 한 피드백 메시지에 함께 들어간다(이 경우 V6: 설계#p1에 새 값 없음)
    assert "Traceback" in fb and "SiteNotFound" in fb and "V6" in fb and "문제일 수도 아닐 수도" in fb
    assert pred["exec_errors"] == 1


def test_export_to_scorer_edits(tmp_path):
    inp, g, ch, res = setup(tmp_path, DEL)
    edits, _ = execute([{"op": "mark_deleted", "req_id": "Req. 2"}], ch, res, g)
    out = to_scorer_edits(edits)
    assert out and all(set(e) == set(PUBLIC_EDIT_KEYS) | {"source"} for e in out)
    assert {e["op"] for e in out} == {"mark_deleted"}


def test_edit_mode_flag_and_schema():
    assert cfg.CascadeConfig().edit_mode == "ops"
    assert cfg.from_dict({"edit_mode": "free_json"}).edit_mode == "free_json"
    with pytest.raises(ValueError):
        cfg.from_dict({"edit_mode": "magic"})
    assert OPS_SCHEMA["properties"]["ops"]["items"]["properties"]["op"]["enum"] == [
        "set_cell", "insert_block", "add_link", "remove_link", "mark_deleted"]
