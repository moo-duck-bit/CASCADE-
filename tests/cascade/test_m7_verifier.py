import pytest

from document_ai.cascade import config as cfg
from document_ai.cascade.apply import apply_edits
from document_ai.cascade.cells import parse_case
from document_ai.cascade.editor import edit, make_edit
from document_ai.cascade.graph import CellGraph
from document_ai.cascade.llm import MockLLM
from document_ai.cascade.locator import locate
from document_ai.cascade.request import parse_request
from document_ai.cascade.trace_log import TraceLog
from document_ai.cascade.verifier import fake_issues, verify

from .fixture_docs import build_case

T1 = "Req. 2 '계정 잠금'의 계정 잠금 시간을 15분에서 30분으로 변경한다."
REL = ["Req. 2 | 설명", "Req. 2 | 기준#p1", "MDDR | Req. 2 | 설계#p1", "IA-07 | 시험방법#p2", "IA-07 | 확인결과",
       "3.4 일반적 제한 | 본문#p1"]


def judge(keep):
    def fn(messages, name):
        lines = [l for l in messages[-1]["content"].splitlines() if l.startswith("- id=")]
        return {"judgments": [{"id": i, "relevant": any(k in l for k in keep), "reason": "t"} for i, l in enumerate(lines)]}
    return fn


@pytest.fixture()
def env(tmp_path):
    inp = build_case(tmp_path / "in")
    g = CellGraph(parse_case(inp))
    return inp, g, tmp_path


def run_verify(env, request, edits, keep=REL, config=None):
    inp, g, tmp = env
    config = config or cfg.from_dict({"verifier_checks": {"V2b": False, "V3b": False, "V6b": False}})  # v1 검사(V2b·V3b·V6b는 test_p5)
    ch = parse_request(request)
    res = locate(ch, g, config, MockLLM(judge(keep)), TraceLog("t"))
    ar = apply_edits(inp, tmp / "out", edits)
    return verify(ch, res, edits, g, parse_case(tmp / "out"), config, ar), res


def types(issues):
    return {(i["type"], i["site"]) for i in issues}


def good_t1_edits():
    return [make_edit("[MDSR | Req. 2 | 설명]", "잠긴 계정은 30분 동안 접근이 차단되어야 한다."),
            make_edit("[MDSR | Req. 2 | 기준#p1]", "30 분 동안 접근 차단을 확인함"),
            make_edit("[MDDR | Req. 2 | 설계#p1]", "• 계정을 삼십 분 동안 잠근다."),
            make_edit("[XXCS | IA-07 | 시험방법#p2]", "계정이 30 분 동안 잠기는지 확인한다."),
            make_edit("[XXCS | IA-07 | 확인결과]", "30분 잠금을 확인함"),
            make_edit("[MDSR | 3.4 일반적 제한 | 본문#p1]", "로그인 5 회 실패 시 계정을 삼십 분 동안 잠근다.")]


def test_clean_t1_has_no_issues(env):
    issues, _ = run_verify(env, T1, good_t1_edits())
    assert issues == []


def test_v1_missing_site(env):
    issues, _ = run_verify(env, T1, good_t1_edits() + [make_edit("[MDSR | Req. 2 | 비고]", "x")])
    assert ("V1", "[MDSR | Req. 2 | 비고]") in types(issues)


def test_v2_old_value_left_and_v6_new_missing(env):
    edits = good_t1_edits()
    edits[1] = make_edit("[MDSR | Req. 2 | 기준#p1]", "15 분 동안 접근 차단을 확인함")
    issues, _ = run_verify(env, T1, edits)
    assert ("V2", "[MDSR | Req. 2 | 기준#p1]") in types(issues)
    assert ("V6", "[MDSR | Req. 2 | 기준#p1]") in types(issues)
    edits[1] = make_edit("[MDSR | Req. 2 | 기준#p1]", "20 분 동안 접근 차단을 확인함")
    issues, _ = run_verify(env, T1, edits)
    assert ("V6", "[MDSR | Req. 2 | 기준#p1]") in types(issues)
    assert ("V2", "[MDSR | Req. 2 | 기준#p1]") not in types(issues)


def test_v2_text_value_contained_in_new(env):
    from document_ai.cascade.verifier import old_left
    assert not old_left("AES-256-GCM 방식으로 암호화", "AES-256", "AES-256-GCM")
    assert old_left("AES 256 방식으로 암호화", "AES-256", "AES-256-GCM")


def test_v5_outside_and_irrelevant(env):
    edits = good_t1_edits() + [make_edit("[MDSR | Req. 1 | 목적]", "바꿈"),            # 후보 밖
                               make_edit("[MDSR | Req. 2 | 기준#p2]", "해제 30분 전 안내")]  # 후보였지만 무관 판정
    issues, _ = run_verify(env, T1, edits)
    assert ("V5", "[MDSR | Req. 1 | 목적]") in types(issues)
    assert ("V5", "[MDSR | Req. 2 | 기준#p2]") in types(issues)
    assert not any(i["type"] == "V5" and i["site"] == "[MDSR | Req. 2 | 설명]" for i in issues)


def test_v3_delete_positive_and_negative(env):
    req = "Req. 2 '계정 잠금' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다."
    inp, g, tmp = env
    ch = parse_request(req)
    res = locate(ch, g, cfg.CascadeConfig(), MockLLM([]), TraceLog("t"))
    good = edit(ch, res, g, cfg.CascadeConfig(), MockLLM([]), TraceLog("t"))
    issues, _ = run_verify(env, req, good, keep=[])
    assert [i for i in issues if i["type"] == "V3"] == []
    bad = [e for e in good if e["site"] != "[XXCS | IA-07 | 해당 요구사항]" and e["site"] != "[MDSR | Req. 2 | 목적]"]
    issues, _ = run_verify(env, req, bad, keep=[])
    assert ("V3", "[XXCS | IA-07 | 해당 요구사항]") in types(issues)
    assert ("V3", "[MDSR | Req. 2 | 목적]") in types(issues)


def test_v3_add_unlinked_and_one_sided(env):
    req = "모바일 애플리케이션 요구사항 Req. 4 '비밀번호 재설정'을(를) 신규 추가한다. 내용: 본인 인증 후 재설정한다."
    ins = [make_edit("[MDSR | Req. 4 | 번호]", "Req. 4", "insert", anchor="Req. 3"),
           make_edit("[MDSR | Req. 4 | 제목]", "비밀번호 재설정", "insert", anchor="Req. 3"),
           make_edit("[MDSR | Req. 4 | 설명]", "재설정한다.", "insert", anchor="Req. 3")]
    issues, _ = run_verify(env, req, ins, keep=["IA-05"])
    assert ("V3", "[MDSR | Req. 4 | 번호]") in types(issues)
    one = ins + [make_edit("[MDSR | IA-05 | 소프트웨어 기능 요구사항 번호]", "Req. 1, Req. 4")]
    issues, _ = run_verify(env, req, one, keep=["IA-05"])
    assert ("V3", "[XXCS | IA-05 | 해당 요구사항]") in types(issues)
    assert ("V3", "[MDSR | Req. 4 | 번호]") not in types(issues)
    both = one + [make_edit("[XXCS | IA-05 | 해당 요구사항]", "Req. 1, Req. 4")]
    issues, _ = run_verify(env, req, both, keep=["IA-05"])
    assert not [i for i in issues if i["type"] == "V3"]


def test_v4_rule_mismatch(env):
    req = "제품 사이버보안 속성 '로컬·네트워크 접속 기능'(remote_access)을 true에서 false로 변경한다."
    good = [make_edit("[XXCS | UC-03 | 체크리스트 적용 여부]", "N/A"), make_edit("[XXCS | UC-03 | 적용 여부]", "N/A"),
            make_edit("[XXCS | UC-03 | 체크리스트 입증 방법]", "해당 없음"), make_edit("[XXCS | UC-03 | 판정]", "해당 없음")]
    issues, _ = run_verify(env, req, good, keep=[])
    assert [i for i in issues if i["type"] == "V4"] == []
    bad = good[:2] + [make_edit("[XXCS | DC-02 | 적용 여부]", "적용")]
    issues, _ = run_verify(env, req, bad, keep=[])
    t = types(issues)
    assert ("V4", "[XXCS | UC-03 | 판정]") in t and ("V4", "[XXCS | UC-03 | 체크리스트 입증 방법]") in t
    assert ("V4", "[XXCS | DC-02 | 적용 여부]") in t  # 다른 속성의 항목을 건드림
    issues, _ = run_verify(env, req, bad, keep=[], config=cfg.preset("no_rules", retrieval=False))
    assert not [i for i in issues if i["type"] == "V4"]


def test_checks_can_be_disabled(env):
    edits = good_t1_edits()
    edits[1] = make_edit("[MDSR | Req. 2 | 기준#p1]", "15 분 동안 접근 차단을 확인함")
    c = cfg.from_dict({"verifier_checks": {"V2": False}})
    issues, _ = run_verify(env, T1, edits, config=c)
    assert not [i for i in issues if i["type"] == "V2"] and [i for i in issues if i["type"] == "V6"]


def test_fake_feedback_same_shape(env):
    inp, g, _ = env
    real = [{"type": "V2", "site": "[MDSR | Req. 2 | 기준#p1]", "detail": "x"},
            {"type": "V2", "site": "[MDDR | Req. 2 | 설계#p1]", "detail": "y"},
            {"type": "V5", "site": "[MDSR | Req. 1 | 목적]", "detail": "z"}]
    f1 = fake_issues(real, g.cells, seed=1, case_id="T1", round_no=0)
    f2 = fake_issues(real, g.cells, seed=1, case_id="T1", round_no=0)
    assert f1 == f2 and len(f1) == 3
    assert sorted(i["type"] for i in f1) == ["V2", "V2", "V5"]
    assert all(set(i) == {"type", "site", "detail"} for i in f1)
    assert {i["site"] for i in f1} <= {c.site for c in g.cells}
