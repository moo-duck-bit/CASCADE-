import json

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.gate import GateError, fingerprint, rollback
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.run import run_case

from .fixture_docs import build_case

T1 = "Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다."
KEEP = ["Req. 2 | 설명", "MDDR | Req. 2 | 설계#p1"]


def scripted(editor_outputs, keep=KEEP):
    """locator는 keep만 관련, editor는 editor_outputs를 차례로 낸다."""
    outs = list(editor_outputs)

    def fn(messages, name):
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": any(k in l for k in keep), "reason": "t"}
                                  for i, l in enumerate(lines)]}
        return outs.pop(0)
    return fn


GOOD = {"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분 동안 접근이 차단되어야 한다."},
                  {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 삼십 분 동안 잠근다."}]}
STALE = {"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분 동안 접근이 차단되어야 한다."},
                   {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 십오 분 동안 잠근다."}]}


@pytest.fixture()
def case(tmp_path):
    return build_case(tmp_path / "case" / "input", request=T1)


def test_issue_then_fix_then_stop(case, tmp_path):
    llm = MockLLM(scripted([STALE, GOOD]))
    pred, log = run_case(case, cfg.from_dict({"edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=llm, work_dir=tmp_path / "w", case_id="c1")
    assert pred["stop_reason"] == "no_issues"
    assert [r["issues"] for r in log["rounds"]] == [2, 0]  # V2 + V6 on 설계#p1
    by = {e["site"]: e["new_text"] for e in pred["edits"]}
    assert by["[MDDR | Req. 2 | 설계#p1]"] == "• 계정을 삼십 분 동안 잠근다."
    assert pred["n_calls"] == 3 and log["llm_calls"] == 3
    assert {"doc", "item", "field", "new_text", "op"} <= set(pred["edits"][0])


def test_stop_no_change(case, tmp_path):
    # P5부터 원문과 같은 글은 편집으로 치지 않으므로, 원문과 다르지만 옛 값이 남은 글을 두 번 낸다
    stale2 = {"edits": [STALE["edits"][0], {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 십오 분간 잠근다."}]}
    llm = MockLLM(scripted([stale2, stale2]))
    pred, log = run_case(case, cfg.from_dict({"edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=llm, work_dir=tmp_path / "w", case_id="c2")
    assert pred["stop_reason"] == "no_change" and len(log["rounds"]) == 2


def test_stop_repeat_issues(case, tmp_path):
    other = {"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분 동안 접근이 막혀야 한다."},
                       {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 십오 분 동안 잠가 둔다."}]}
    llm = MockLLM(scripted([STALE, other]))
    pred, log = run_case(case, cfg.from_dict({"edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=llm, work_dir=tmp_path / "w", case_id="c3")
    assert pred["stop_reason"] == "repeat_issues"


def test_stop_max_rounds(case, tmp_path):
    # 지적이 라운드마다 달라야 반복 멈춤에 걸리지 않는다: {V2,V6} → {V6} → {V2,V6}
    outs = [STALE] + [{"edits": [{"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": t}]}
                      for t in ("• 20 분 잠근다.", "• 십오 분 잠근다.")]
    llm = MockLLM(scripted(outs))
    pred, log = run_case(case, cfg.from_dict({"edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=llm, work_dir=tmp_path / "w", case_id="c4")
    assert pred["stop_reason"] == "max_rounds" and len(log["rounds"]) == 3 and pred["n_calls"] == 4


def test_stop_token_budget(case, tmp_path):
    llm = MockLLM(scripted([STALE, GOOD]))
    pred, log = run_case(case, cfg.from_dict({"token_budget": 10, "edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=llm, work_dir=tmp_path / "w", case_id="c5")
    assert pred["stop_reason"] == "token_budget" and pred["n_calls"] == 2


def test_no_verifier_single_pass(case, tmp_path):
    llm = MockLLM(scripted([STALE]))
    pred, log = run_case(case, cfg.preset("no_verifier", edit_mode="free_json", verifier_checks={"V2b": False, "V3b": False, "V6b": False}, retrieval=False), llm=llm, work_dir=tmp_path / "w", case_id="c6")
    assert pred["stop_reason"] == "verifier_off" and pred["n_calls"] == 2


def test_gate_rollback_outside_edit(case, tmp_path):
    out = {"edits": GOOD["edits"] + [{"site": "[MDSR | Req. 1 | 목적]", "new_text": "몰래 바꿈"}]}
    llm = MockLLM(scripted([out, out]))
    pred, log = run_case(case, cfg.from_dict({"edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=llm, work_dir=tmp_path / "w", case_id="c7")
    sites = {e["site"] for e in pred["edits"]}
    assert "[MDSR | Req. 1 | 목적]" not in sites
    assert log["gate"]["rolled_back"] == ["[MDSR | Req. 1 | 목적]"]
    assert log["gate"]["approved"] is True and log["gate"]["fingerprint_ok"] is True
    assert "structural_deficit" in log["gate"] and "consistency_regression" in log["gate"]


def test_rollback_unit():
    edits = [{"site": "a"}, {"site": "b"}, {"site": "c"}]
    kept, back = rollback(edits, [{"type": "V5", "site": "b", "detail": ""}, {"type": "V2", "site": "c", "detail": ""}])
    assert [e["site"] for e in kept] == ["a", "c"] and back == ["b"]


def test_fingerprint_detects_change(case):
    fp = fingerprint(case)
    (case / "MDSR.docx").write_bytes((case / "MDSR.docx").read_bytes() + b"x")
    assert fingerprint(case) != fp


def test_run_case_reads_only_input_and_logs(case, tmp_path):
    (case.parent / "gold").mkdir()
    (case.parent / "gold" / "impact.json").write_text("{}", encoding="utf-8")
    llm = MockLLM(scripted([GOOD]))
    pred, log = run_case(case, cfg.from_dict({"edit_mode": "free_json", "verifier_checks": {"V2b": False, "V3b": False, "V6b": False}}), llm=llm, work_dir=tmp_path / "w", case_id="c8")
    assert log["violations"] == []
    assert all("/gold/" not in p for p in log["files_read"])
    assert any(p.endswith("request.txt") for p in log["files_read"])
    saved = json.loads((tmp_path / "w" / "log.json").read_text(encoding="utf-8"))
    assert saved["case_id"] == "c8" and pred["log_path"].endswith("log.json")


def test_fake_feedback_mode_uses_fake_issues(case, tmp_path):
    llm = MockLLM(scripted([STALE, GOOD]))
    pred, log = run_case(case, cfg.preset("fake_feedback", edit_mode="free_json", verifier_checks={"V2b": False, "V3b": False, "V6b": False}, retrieval=False), llm=llm, work_dir=tmp_path / "w", case_id="c9")
    fb = llm.calls[2]["messages"][-1]["content"]
    assert "검사에서 문제가 있을 수 있는 칸으로 표시됨" in fb
    assert log["rounds"][0]["real_issues"] == 2 and log["rounds"][0]["issues"] == 2
