"""가짜 미니 도메인 문서 세트. EC-SW와 문서 이름·항목 번호·연결 표가 모두 다르다."""

from pathlib import Path

from docx import Document

PACK = Path(__file__).resolve().parent / "data" / "mini_pack" / "pack.yaml"
REQS = {"FR-1": ("화면 꺼짐", "입력이 없으면 30초 뒤 화면을 끈다.", ["30초 뒤 꺼짐을 확인한다"]),
        "FR-2": ("경고음", "배터리가 20% 이하이면 경고음을 3회 낸다.", ["20% 경고를 확인한다", "3회 울림을 확인한다"]),
        "FR-3": ("자동 저장", "기록은 30초마다 저장한다.", ["저장 주기를 확인한다"])}
TESTS = [("TC-01", "30초 무입력 후 화면 꺼짐 확인", "FR-1"), ("TC-02", "배터리 20% 경고음 확인", "FR-2"),
         ("TC-03", "저장 주기 30초 확인", "FR-3")]


def build_mini(dirpath, request=None):
    inp = Path(dirpath)
    inp.mkdir(parents=True, exist_ok=True)
    d = Document()
    d.add_paragraph("기능 명세")
    d.add_paragraph("1 기능 3")
    d.add_paragraph("기능")
    for rid, (title, desc, crit) in REQS.items():
        t = d.add_table(rows=3, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = rid, title
        t.cell(1, 0).text, t.cell(1, 1).text = "설명", desc
        t.cell(2, 0).text, t.cell(2, 1).text = "기준", crit[0]
        for extra in crit[1:]:
            t.cell(2, 1).add_paragraph(extra)
        d.add_paragraph("")
    d.save(inp / "SPEC.docx")
    m = Document()
    m.add_paragraph("사용 설명서")
    m.add_paragraph("1 사용법 2")
    m.add_paragraph("사용법")
    for rid, (title, desc, _) in REQS.items():
        m.add_paragraph(rid)
        m.add_paragraph(f"- {desc}")
    m.save(inp / "MAN.docx")
    t = Document()
    t.add_paragraph("시험 기록")
    tb = t.add_table(rows=1 + len(TESTS), cols=3)
    for j, h in enumerate(("시험 번호", "시험 내용", "대상 기능")):
        tb.cell(0, j).text = h
    for i, row in enumerate(TESTS, 1):
        for j, v in enumerate(row):
            tb.cell(i, j).text = v
    t.save(inp / "TEST.docx")
    if request:
        (inp / "request.txt").write_text(request, encoding="utf-8")
    return inp
