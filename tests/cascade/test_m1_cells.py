import os
from pathlib import Path

import pytest

from document_ai.cascade import pack as pack_mod
from document_ai.cascade.cells import index_lines, parse_case, parse_doc

PILOT = Path(os.environ.get("PILOT_DATA", "/home/jkpark/workspace/pilot_data"))
CASES = PILOT / "v02" / "cases"
pytestmark = pytest.mark.skipif(not CASES.exists(), reason="pilot_data v0.2 cases not found")

SAMPLE = ("T1_01", "T2_01", "T3_03")  # 문서 3종 / XXCS만 / MDSR+MDDR


@pytest.mark.parametrize("cid", SAMPLE)
def test_cells_match_site_index_exactly(cid):
    inp = CASES / cid / "input"
    cells = parse_case(inp)
    expected = (inp / "site_index.md").read_text(encoding="utf-8").splitlines()
    assert index_lines(cells) == expected


@pytest.mark.skipif(not os.environ.get("CASCADE_ALL_CASES"), reason="set CASCADE_ALL_CASES=1 for all 36")
def test_cells_match_all_cases():
    for d in sorted(CASES.glob("T*")):
        cells = parse_case(d / "input")
        assert index_lines(cells) == (d / "input" / "site_index.md").read_text(encoding="utf-8").splitlines(), d.name


def test_cells_have_writable_paragraph_and_doc_order():
    cells = parse_doc("MDSR", CASES / "T1_01" / "input" / "MDSR.docx")
    assert all(c.para is not None for c in cells)
    req = [c for c in cells if c.item == "Req. 2"]
    assert [c.cell for c in req][:4] == ["번호", "제목", "설명", "목적"]
    assert req[0].block is not None  # Req 표(삽입 앵커)
    assert all(c.doc == "MDSR" for c in cells)


def test_multi_paragraph_and_column_names():
    cells = parse_case(CASES / "T1_01" / "input")
    names = {c.site for c in cells}
    assert "[MDSR | Req. 2 | 기준#p1]" in names and "[MDSR | Req. 2 | 기준#p2]" in names
    assert any("#c" in c.cell for c in cells)  # 다열 칸
    assert "[MDDR | Req. 2 | 설계#p1]" in names
    assert "[XXCS | IA-07 | 해당 요구사항]" in names
    assert set(pack_mod.load("ec_sw").docs) == {"MDSR", "MDDR", "XXCS"}
