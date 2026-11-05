from collections import Counter

import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.run import run_case

from .fixture_docs import build_case

T1 = "Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다."
T2 = "제품 사이버보안 속성 '로컬·네트워크 접속 기능'(remote_access)을 true에서 false로 변경한다."
STALE = {"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분 동안 접근이 차단되어야 한다."},
                   {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 십오 분 동안 잠근다."}]}
GOOD = {"edits": [{"site": "[MDSR | Req. 2 | 설명]", "new_text": "잠긴 계정은 30분 동안 접근이 차단되어야 한다."},
                  {"site": "[MDDR | Req. 2 | 설계#p1]", "new_text": "• 계정을 삼십 분 동안 잠근다."}]}


def responder(editor_outs, keep=("Req. 2 | 설명", "MDDR | Req. 2 | 설계#p1")):
    outs = list(editor_outs)

    def fn(messages, name):
        if name == "locator":
            lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
            return {"judgments": [{"id": i, "relevant": any(k in l for k in keep), "reason": "t"}
                                  for i, l in enumerate(lines)]}
        return outs.pop(0) if outs else {"edits": []}
    return fn


def run(tmp_path, request, preset, outs, keep=None):
    case = build_case(tmp_path / preset / "input", request=request)
    llm = MockLLM(responder(outs, keep) if keep else responder(outs))
    pred, log = run_case(case, cfg.preset(preset, edit_mode="free_json", verifier_checks={"V2b": False, "V3b": False, "V6b": False}, retrieval=False), llm=llm, work_dir=tmp_path / preset / "w", case_id=preset)
    return pred, log, Counter(c["name"] for c in llm.calls)


def test_full_uses_all_components(tmp_path):
    pred, log, calls = run(tmp_path, T1, "full", [STALE, GOOD])
    assert calls == Counter({"locator": 1, "editor": 1, "editor_r1": 1})
    assert log["stages"]["locator"]["relevant"] == 2


def test_no_verifier_turns_off_loop(tmp_path):
    pred, log, calls = run(tmp_path, T1, "no_verifier", [STALE])
    assert "editor_r1" not in calls and pred["stop_reason"] == "verifier_off"


def test_no_relevance_skips_judgment(tmp_path):
    pred, log, calls = run(tmp_path, T1, "no_relevance", [GOOD])
    assert "locator" not in calls
    assert log["stages"]["locator"]["relevant"] == log["stages"]["locator"]["candidates"] > 2


def test_no_value_index_uses_literal(tmp_path):
    _, log_full, _ = run(tmp_path, T1, "full", [GOOD])
    _, log_nv, _ = run(tmp_path, T1, "no_value_index", [GOOD])
    assert log_nv["stages"]["locator"]["candidates"] < log_full["stages"]["locator"]["candidates"]


def test_no_rules_turns_off_rule_engine_and_v4(tmp_path):
    pred, log, calls = run(tmp_path, T2, "no_rules", [{"edits": []}], keep=("UC-03",))
    assert calls["locator"] == 1  # 규칙 대신 LLM이 항목을 고른다
    assert log["locator"]["rule_info"]["flips"][0]["rule"] is None
    assert all(r["by_type"].get("V4", 0) == 0 for r in log["rounds"])
    pred_f, log_f, calls_f = run(tmp_path, T2, "full", [])
    assert calls_f.get("locator", 0) == 1 and log_f["locator"]["rule_info"]["flips"][0]["rule"] == "R05"


def test_fake_feedback_replaces_issue_sites(tmp_path):
    pred, log, calls = run(tmp_path, T1, "fake_feedback", [STALE, GOOD])
    assert calls["editor_r1"] == 1
    assert log["rounds"][0]["real_issues"] == log["rounds"][0]["issues"]


def test_ledger_like_no_relevance_no_loop(tmp_path):
    pred, log, calls = run(tmp_path, T1, "ledger_like", [STALE])
    assert set(calls) == {"editor"} and pred["stop_reason"] == "report_only"
    assert log["stages"]["locator"]["mode"] == "ledger_like"
    assert log["rounds"][0]["real_issues"] >= 0 and len(log["rounds"]) == 1


@pytest.mark.parametrize("name", cfg.PRESETS)
def test_every_preset_runs(tmp_path, name):
    pred, log, _ = run(tmp_path, T1, name, [GOOD, GOOD, GOOD])
    assert pred["error"] is None and pred["case_type"] == "T1"
