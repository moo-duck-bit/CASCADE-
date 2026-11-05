import os
from pathlib import Path

import pytest
from lxml import etree

from document_ai.cascade.apply import apply_edits
from document_ai.cascade.cells import parse_case, parse_doc
from document_ai.cascade.editor import make_edit

from .fixture_docs import build_case

PILOT = Path(os.environ.get("PILOT_DATA", str(Path(__file__).resolve().parents[2])))


def snapshot(input_dir):
    return {(c.site, i): (c.text, etree.tostring(c.para._p))
            for i, c in enumerate(parse_case(input_dir))}


def by_site(input_dir):
    return {c.site: c.text for c in parse_case(input_dir)}


def test_modify_only_edited_cells_change(tmp_path):
    inp = build_case(tmp_path / "in")
    before = snapshot(inp)
    edits = [make_edit("[MDSR | Req. 2 | 설명]", "잠긴 계정은 30분 동안 접근이 차단되어야 한다."),
             make_edit("[XXCS | IA-07 | 시험방법#p2]", "계정이 30 분 동안 잠기는지 확인한다.")]
    res = apply_edits(inp, tmp_path / "out", edits)
    assert res.failed == [] and sorted(res.applied) == sorted(e["site"] for e in edits)
    after = snapshot(tmp_path / "out")
    assert len(after) == len(before)
    changed = {k[0] for k in before if before[k] != after.get(k)}
    assert changed == {"[MDSR | Req. 2 | 설명]", "[XXCS | IA-07 | 시험방법#p2]"}
    assert by_site(tmp_path / "out")["[MDSR | Req. 2 | 설명]"] == "잠긴 계정은 30분 동안 접근이 차단되어야 한다."
    assert by_site(inp)["[MDSR | Req. 2 | 설명]"].startswith("잠긴 계정은 15분")  # 원본은 그대로


def test_multi_paragraph_cell_no_duplication_no_stale(tmp_path):
    inp = build_case(tmp_path / "in")
    edits = [make_edit("[XXCS | IA-07 | 시험방법#p2]", "계정이 30 분 동안 잠기는지 확인한다."),
             make_edit("[MDSR | Req. 2 | 기준#p1]", "30 분 동안 접근 차단을 확인함")]
    apply_edits(inp, tmp_path / "out", edits)
    cells = [c for c in parse_case(tmp_path / "out") if c.item in ("IA-07", "Req. 2")]
    texts = [c.text for c in cells]
    sites = [c.site for c in cells]
    assert len(sites) == len(set(sites))  # 칸이 늘거나 겹치지 않는다
    assert "5 회 연속 실패를 입력한다." in texts  # 같은 칸 #p1 유지
    assert texts.count("계정이 30 분 동안 잠기는지 확인한다.") == 1
    assert "계정이 15 분 동안 잠기는지 확인한다." not in texts
    assert "잠금 해제 15분 전에 안내함을 확인함" in texts  # 같은 칸 #p2 유지


def test_mark_deleted_and_run_format_kept(tmp_path):
    inp = build_case(tmp_path / "in")
    doc = parse_doc("MDSR", inp / "MDSR.docx")
    p = next(c for c in doc if c.site == "[MDSR | Req. 3 | 설명]").para
    p.runs[0].bold = True
    p.part.document.save(str(inp / "MDSR.docx"))
    apply_edits(inp, tmp_path / "out", [make_edit("[MDSR | Req. 3 | 설명]", "삭제", "mark_deleted")])
    c = next(c for c in parse_doc("MDSR", tmp_path / "out" / "MDSR.docx") if c.site == "[MDSR | Req. 3 | 설명]")
    assert c.text == "삭제" and c.para.runs[0].bold is True


def test_insert_new_req_after_anchor(tmp_path):
    inp = build_case(tmp_path / "in")
    edits = [make_edit("[MDSR | Req. 4 | 번호]", "Req. 4", "insert", anchor="Req. 3"),
             make_edit("[MDSR | Req. 4 | 제목]", "비밀번호 재설정", "insert", anchor="Req. 3"),
             make_edit("[MDSR | Req. 4 | 설명]", "본인 인증 후 비밀번호를 재설정하여야 한다.", "insert", anchor="Req. 3"),
             make_edit("[MDSR | Req. 4 | 목적]", "계정 접근을 회복한다.", "insert", anchor="Req. 3"),
             make_edit("[MDSR | Req. 4 | 기준#p1]", "본인 인증을 확인함", "insert", anchor="Req. 3"),
             make_edit("[MDSR | Req. 4 | 기준#p2]", "재설정 완료를 확인함", "insert", anchor="Req. 3"),
             make_edit("[MDDR | Req. 4 | 번호]", "Req. 4", "insert", anchor="Req. 3"),
             make_edit("[MDDR | Req. 4 | 설계#p1]", "• 인증 코드를 보낸다.", "insert", anchor="Req. 3"),
             make_edit("[MDDR | Req. 4 | 설계#p2]", "• 새 비밀번호를 저장한다.", "insert", anchor="Req. 3")]
    res = apply_edits(inp, tmp_path / "out", edits)
    assert res.failed == []
    cells = parse_case(tmp_path / "out")
    order = [c.site for c in cells]
    got = {c.site: c.text for c in cells}
    for e in edits:
        assert got[e["site"]] == e["new_text"], e["site"]
    assert order.index("[MDSR | Req. 4 | 번호]") > order.index("[MDSR | Req. 3 | 기준]")
    assert order.index("[MDSR | Req. 4 | 기준#p2]") < order.index("[MDSR | Req. 100 | 번호]")
    assert order.index("[MDDR | Req. 4 | 번호]") > order.index("[MDDR | Req. 3 | 설계]")
    assert got["[MDSR | Req. 3 | 설명]"].startswith("세션은")  # 앵커는 그대로
    before = by_site(inp)
    assert all(got[s] == t for s, t in before.items())  # 기존 칸은 모두 그대로


def test_missing_site_reported(tmp_path):
    inp = build_case(tmp_path / "in")
    res = apply_edits(inp, tmp_path / "out", [make_edit("[MDSR | Req. 9 | 설명]", "x"),
                                              make_edit("[MDDR | Req. 1 | 설계#p1]", "• 바뀜")],
                      )
    assert [f["site"] for f in res.failed] == ["[MDSR | Req. 9 | 설명]"]
    assert res.applied == ["[MDDR | Req. 1 | 설계#p1]"]


@pytest.mark.skipif(not (PILOT / "v02" / "cases").exists(), reason="pilot_data not found")
def test_real_case_multiparagraph_regression(tmp_path):
    inp = PILOT / "v02" / "cases" / "T1_01" / "input"
    before = snapshot(inp)
    edits = [make_edit("[MDSR | Req. 2 | 기준#p3]", "10 분 에 사용자에게 안내 알림이 전송됨을 확인함"),
             make_edit("[MDDR | Req. 2 | 설계#p3]", "• 잠금 해제 10분 전에 안내 메시지를 표시하도록 구현한다."),
             make_edit("[XXCS | IA-02 | 확인결과#p1]", "사용자의 십 분 동안의 실패 로그인 시도 기록이 정상적으로 수행됨을 확인함")]
    res = apply_edits(inp, tmp_path / "out", edits)
    assert res.failed == []
    after = snapshot(tmp_path / "out")
    assert len(after) == len(before)
    changed = {k[0] for k in before if before[k] != after.get(k)}
    assert changed == {e["site"] for e in edits}
