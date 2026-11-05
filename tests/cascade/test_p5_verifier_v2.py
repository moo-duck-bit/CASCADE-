"""P5: Verifier v2. Locator와 독립인 V2b·V3b·V6b 양성·음성, V5의 범위 확장, 고리에서 지적 칸을 Editor 목록에 더하기."""

from document_ai.cascade import config as cfg
from document_ai.cascade.apply import apply_edits
from document_ai.cascade.cells import parse_case
from document_ai.cascade.editor import make_edit
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import Cand, LocResult, locate
from document_ai.cascade.request import parse_request
from document_ai.cascade.run import run_case
from document_ai.cascade.trace_log import TraceLog
from document_ai.cascade.verifier import verify

from .fixture_docs import build_case
from .test_m8_loop_gate import T1

ADD = "모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 이메일 인증 후 비밀번호를 재설정한다."
DEL = "Req. 2 '계정 잠금' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다."
ONLY_V2 = {"V1": False, "V2": False, "V3": False, "V4": False, "V5": False, "V6": False}


def t1_setup(tmp_path, keep=("[MDSR | Req. 2 | 설명]",)):
    inp = build_case(tmp_path / "case" / "input", request=T1)
    g = CellGraph(parse_case(inp))
    ch = parse_request(T1)
    cands = [Cand(s, "origin", g.cell(s)) for s in keep]
    return inp, g, ch, LocResult(cands, list(cands))


def run_verify(inp, g, ch, res, edits, tmp, checks):
    out = tmp / "out"
    ar = apply_edits(inp, out, edits)
    conf = cfg.from_dict({"verifier_checks": {**ONLY_V2, **checks}})
    return verify(ch, res, edits, g, parse_case(out), conf, ar)


def sites(issues, t):
    return {i["site"] for i in issues if i["type"] == t}


def test_v2b_flags_old_value_in_origin_same_id_and_trace_rows(tmp_path):
    inp, g, ch, res = t1_setup(tmp_path)
    edits = [make_edit("[MDSR | Req. 2 | 설명]", "잠긴 계정은 30분 동안 접근이 차단되어야 한다.")]
    issues = run_verify(inp, g, ch, res, edits, tmp_path, {"V2b": True})
    s = sites(issues, "V2b")
    assert "[MDSR | Req. 2 | 기준#p1]" in s                 # 출발 Req
    assert "[MDDR | Req. 2 | 설계#p1]" in s                 # 같은 번호 항목
    assert "[XXCS | IA-07 | 확인결과]" in s                 # trace 행
    assert "[MDSR | Req. 2 | 설명]" not in s
    assert "[MDSR | Req. 3 | 설명]" not in s                # 범위 밖(V6b 몫)


def test_v2b_negative_when_scope_fixed(tmp_path):
    inp, g, ch, res = t1_setup(tmp_path)
    scope = [c for c in g.cells if (c.item == "Req. 2" or c.item == "IA-07") and c.base != "번호"
             and g.values.key_of("15분") in {m.key for m in g.values.mentions(c.text)}]
    edits = [make_edit(c.site, c.text.replace("15", "30").replace("십오", "삼십")) for c in scope]
    issues = run_verify(inp, g, ch, res, edits, tmp_path, {"V2b": True})
    assert sites(issues, "V2b") == set()


def test_v6b_flags_same_value_unedited_outside_scope(tmp_path):
    inp, g, ch, res = t1_setup(tmp_path)
    edits = [make_edit("[MDSR | Req. 2 | 설명]", "30분")]
    issues = run_verify(inp, g, ch, res, edits, tmp_path, {"V6b": True})
    s = sites(issues, "V6b")
    assert "[MDSR | Req. 3 | 설명]" in s                    # 십오 분(세션) — 같은 값, 다른 사실일 수 있음
    assert "[XXCS | UC-03 | 시험방법]" in s
    assert "[MDSR | Req. 2 | 기준#p1]" not in s             # V2b 범위
    assert all("확인" in i["detail"] for i in issues if i["type"] == "V6b")


def test_v6b_negative_when_edited_or_other_value(tmp_path):
    inp, g, ch, res = t1_setup(tmp_path)
    edits = [make_edit("[MDSR | Req. 3 | 설명]", "세션은 십오 분 동안 활동이 없으면 종료되어야 한다(유지).")]
    issues = run_verify(inp, g, ch, res, edits, tmp_path, {"V6b": True})
    assert "[MDSR | Req. 3 | 설명]" not in sites(issues, "V6b")
    assert "[MDSR | Req. 100 | 설명]" not in sites(issues, "V6b")   # 60분은 다른 값


def test_v6b_independent_of_value_index_flag(tmp_path):
    inp, g, ch, res = t1_setup(tmp_path)
    out = tmp_path / "o"
    edits = [make_edit("[MDSR | Req. 2 | 설명]", "30분")]
    ar = apply_edits(inp, out, edits)
    conf = cfg.from_dict({"value_index": False, "verifier_checks": {**ONLY_V2, "V6b": True}})
    issues = verify(ch, res, edits, g, parse_case(out), conf, ar)
    assert "[MDSR | Req. 3 | 설명]" in sites(issues, "V6b")   # 한글 수사 '십오 분'도 잡음


def add_setup(tmp_path):
    inp = build_case(tmp_path / "case" / "input", request=ADD)
    g = CellGraph(parse_case(inp))
    ch = parse_request(ADD)
    res = locate(ch, g, cfg.from_dict({"relevance": False}), None, TraceLog("t"))
    return inp, g, ch, res


def test_v3b_add_missing_block_in_one_doc(tmp_path):
    inp, g, ch, res = add_setup(tmp_path)
    edits = [make_edit("[MDSR | Req. 4 | 번호]", "Req. 4", "insert", "rule", "insert", "Req. 3"),
             make_edit("[MDSR | UC-03 | 소프트웨어 기능 요구사항 번호]", "Req. 3, Req. 4", "modify", "rule", "link"),
             make_edit("[XXCS | UC-03 | 해당 요구사항]", "Req. 3, Req. 4", "modify", "rule", "link")]
    issues = run_verify(inp, g, ch, res, edits, tmp_path, {"V3b": True})
    assert sites(issues, "V3b") == {"[MDDR | Req. 4 | 번호]"}
    edits.append(make_edit("[MDDR | Req. 4 | 번호]", "Req. 4", "insert", "rule", "insert", "Req. 3"))
    edits.append(make_edit("[MDDR | Req. 4 | 설계#p1]", "• 재설정", "insert", "llm", "insert", "Req. 3"))
    issues = run_verify(inp, g, ch, res, edits, tmp_path / "2", {"V3b": True})
    assert sites(issues, "V3b") == set()


def test_v3b_add_one_sided_link(tmp_path):
    inp, g, ch, res = add_setup(tmp_path)
    edits = [make_edit("[MDSR | Req. 4 | 번호]", "Req. 4", "insert", "rule", "insert", "Req. 3"),
             make_edit("[MDDR | Req. 4 | 번호]", "Req. 4", "insert", "rule", "insert", "Req. 3"),
             make_edit("[MDDR | Req. 4 | 설계#p1]", "• 재설정", "insert", "llm", "insert", "Req. 3"),
             make_edit("[MDSR | UC-03 | 소프트웨어 기능 요구사항 번호]", "Req. 3, Req. 4", "modify", "rule", "link")]
    issues = run_verify(inp, g, ch, res, edits, tmp_path, {"V3b": True})
    assert sites(issues, "V3b") == {"[XXCS | UC-03 | 해당 요구사항]"}


def test_v3b_delete_remaining_link_and_dedupe_with_v3(tmp_path):
    inp = build_case(tmp_path / "case" / "input", request=DEL)
    g = CellGraph(parse_case(inp))
    ch = parse_request(DEL)
    res = locate(ch, g, cfg.CascadeConfig(), None, TraceLog("t"))
    edits = [make_edit("[MDSR | IA-07 | 소프트웨어 기능 요구사항 번호]", "Req. 1", "modify", "rule", "unlink")]
    issues = run_verify(inp, g, ch, res, edits, tmp_path, {"V3b": True})
    assert sites(issues, "V3b") == {"[XXCS | IA-07 | 해당 요구사항]"}
    issues = run_verify(inp, g, ch, res, edits, tmp_path / "2", {"V3b": True, "V3": True})
    assert "[XXCS | IA-07 | 해당 요구사항]" in sites(issues, "V3")
    assert "[XXCS | IA-07 | 해당 요구사항]" not in sites(issues, "V3b")


def test_v5_skips_verifier_scope(tmp_path):
    inp, g, ch, res = t1_setup(tmp_path)
    edits = [make_edit("[XXCS | IA-07 | 확인결과]", "30분 잠금을 확인함")]
    assert "[XXCS | IA-07 | 확인결과]" in sites(run_verify(inp, g, ch, res, edits, tmp_path, {"V5": True}), "V5")
    res.verifier_scope.add("[XXCS | IA-07 | 확인결과]")
    assert sites(run_verify(inp, g, ch, res, edits, tmp_path / "2", {"V5": True}), "V5") == set()


def test_checks_registered_in_config():
    assert {"V2b", "V3b", "V6b"} <= set(cfg.CHECKS)
    assert all(cfg.CascadeConfig().verifier_checks[k] for k in ("V2b", "V3b", "V6b"))


def test_loop_adds_flagged_cells_to_editor_and_drops_noop(tmp_path):
    keep = ("Req. 2 | 설명", "MDDR | Req. 2 | 설계#p1")
    seen = []

    def fn(messages, name):
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": any(k in l for k in keep), "reason": "t"}
                                  for i, l in enumerate(lines)]}
        seen.append(messages[-1]["content"] if name != "editor" else messages[1]["content"])
        if name == "editor":
            return {"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분 동안 접근이 차단되어야 한다."},
                              {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 삼십 분 동안 잠근다."}]}
        # 재수정: 지적 칸 가운데 같은 사실인 XXCS 확인결과만 고치고, 세션 칸(Req. 3)은 원문 그대로 낸다
        return {"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분 동안 접근이 차단되어야 한다."},
                          {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 삼십 분 동안 잠근다."},
                          {"site": "[XXCS | IA-07 | 확인결과]", "new_text": "30분 잠금을 확인함"},
                          {"site": "[MDSR | Req. 3 | 설명]", "new_text": "세션은 십오 분 동안 활동이 없으면 종료되어야 한다."}]}
    inp = build_case(tmp_path / "case" / "input", request=T1)
    conf = cfg.from_dict({"edit_mode": "free_json", "loop_k": 1})
    pred, log = run_case(inp, conf, llm=MockLLM(fn), work_dir=tmp_path / "w")
    assert "V2b" in log["rounds"][0]["by_type"] and "V6b" in log["rounds"][0]["by_type"]
    fb = seen[-1]
    assert "[XXCS | IA-07 | 확인결과]" in fb
    by = {e["site"]: e["new_text"] for e in pred["edits"]}
    assert by["[XXCS | IA-07 | 확인결과]"] == "30분 잠금을 확인함"      # 범위 밖이었지만 V2b가 넓힌 범위라 되돌리지 않음
    assert "[MDSR | Req. 3 | 설명]" not in by                            # 원문과 같은 글은 편집이 아님
