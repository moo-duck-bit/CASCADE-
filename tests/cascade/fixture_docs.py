"""작은 합성 문서 세트(MDSR·MDDR·XXCS). 실제 양식과 같은 구조 규칙만 따른다."""

from pathlib import Path

from docx import Document

from document_ai.cascade.cells import index_lines, parse_case

MDSR_REQS = {
    "Req. 1": ("사용자 로그인", "비밀번호는 8자 이상이어야 하며 로그인 5회 실패 시 계정이 잠겨야 한다.",
               "무단 접근을 막는다.", ["8 자 미만 비밀번호가 거부됨을 확인함", "로그인 5 회 실패 시 잠금을 확인함"]),
    "Req. 2": ("계정 잠금", "잠긴 계정은 15분 동안 접근이 차단되어야 한다.",
               "무차별 대입을 막는다.", ["15 분 동안 접근 차단을 확인함", "잠금 해제 15분 전에 안내함을 확인함"]),
    "Req. 3": ("세션 관리", "세션은 십오 분 동안 활동이 없으면 종료되어야 한다.",
               "자리 비움 시 노출을 막는다.", ["15분 비활동 후 세션 종료를 확인함"]),
    "Req. 100": ("데이터 동기화", "서버는 60분마다 기록을 동기화하여야 한다.",
                 "의료진이 최신 기록을 본다.", ["60 분 주기 동기화를 확인함", "1시간 이내 반영을 확인함"]),
}
SEC_ROWS = [  # 번호, 항목, 요구사항, 연결 Req, 적용, 입증, 시험방법, 확인결과, 판정
    ("IA-05", "식별 및 인증(IA)", "비밀번호 강도 설정", "Req. 1", "적용", "비밀번호 규칙 확인",
     ["8 자 미만 비밀번호 입력을 시도한다."], ["거부됨을 확인함"], "만족"),
    ("IA-07", "식별 및 인증(IA)", "연속적인 로그인 시도 실패 시 로그인 제한", "Req. 1, Req. 2", "적용", "잠금 기능 확인",
     ["5 회 연속 실패를 입력한다.", "계정이 15 분 동안 잠기는지 확인한다."], ["15분 잠금을 확인함"], "만족"),
    ("UC-03", "사용 통제(UC)", "세션 잠금", "Req. 3", "적용", "세션 종료 확인",
     ["15 분 동안 입력하지 않는다."], ["세션 종료를 확인함"], "만족"),
    ("DC-02", "데이터 기밀성(DC)", "보건의료정보 비식별화", "해당 없음", "N/A", "해당 없음",
     ["해당 없음"], ["해당 없음"], "해당 없음"),
    ("SI-01", "시스템 무결성(SI)", "통신에 대한 무결성 보장", "Req. 1, Req. 100", "적용", "TLS 확인",
     ["60 분 주기 동기화 패킷을 확인한다."], ["무결성 확인함"], "만족"),
]


def _toc(doc, entries):
    for num, title, page in entries:
        doc.add_paragraph(f"{num} {title} {page}")


def build_mdsr(path):
    d = Document()
    d.add_paragraph("소프트웨어 요구사항 명세서")
    _toc(d, [("3.4", "일반적 제한", 8), ("5.1", "모바일 애플리케이션", 17), ("5.2", "서버", 20),
             ("6", "사이버 보안 필수 요구사항", 33)])
    d.add_paragraph("일반적 제한")
    d.add_paragraph("로그인 5 회 실패 시 계정을 십오 분 동안 잠근다.")
    d.add_paragraph("세션은 15분 비활동 시 종료된다.")
    for heading, reqs in (("모바일 애플리케이션", ["Req. 1", "Req. 2", "Req. 3"]), ("서버", ["Req. 100"])):
        d.add_paragraph(heading)
        for rid in reqs:
            title, desc, purpose, crit = MDSR_REQS[rid]
            t = d.add_table(rows=4, cols=2)
            t.cell(0, 0).text, t.cell(0, 1).text = rid, title
            t.cell(1, 0).text, t.cell(1, 1).text = "설명", desc
            t.cell(2, 0).text, t.cell(2, 1).text = "목적", purpose
            t.cell(3, 0).text = "기준"
            t.cell(3, 1).text = crit[0]
            for extra in crit[1:]:
                t.cell(3, 1).add_paragraph(extra)
            d.add_paragraph("")
    d.add_paragraph("사이버 보안 필수 요구사항")
    t = d.add_table(rows=1 + len(SEC_ROWS), cols=4)
    for j, h in enumerate(("사이버보안 요구사항 번호", "항목", "요구사항", "소프트웨어 기능 요구사항 번호")):
        t.cell(0, j).text = h
    for i, row in enumerate(SEC_ROWS, 1):
        for j in range(4):
            t.cell(i, j).text = row[j]
    d.save(path)


def build_mddr(path):
    d = Document()
    d.add_paragraph("소프트웨어 설계 명세서")
    _toc(d, [("5.1", "모바일 애플리케이션", 3), ("5.2", "서버", 6)])
    d.add_paragraph("모바일 애플리케이션")
    for rid, paras in (("Req. 1", ["• 비밀번호가 8자 미만이면 거부한다.", "• 5번 실패하면 잠근다."]),
                       ("Req. 2", ["• 계정을 십오 분 동안 잠근다.", "• 해제 15분 전에 안내한다."]),
                       ("Req. 3", ["• 15 분 비활동 시 세션을 끊는다."])):
        d.add_paragraph(rid)
        for p in paras:
            d.add_paragraph(p)
    d.add_paragraph("서버")
    d.add_paragraph("Req. 100")
    d.add_paragraph("• 60분 주기로 동기화한다.")
    d.save(path)


def build_xxcs(path):
    d = Document()
    d.add_paragraph("소프트웨어 보안 검증 보고서")
    t = d.add_table(rows=1 + len(SEC_ROWS), cols=6)
    for j, h in enumerate(("분류", "순번", "번호", "요구사항", "해당기기 적용여부", "입증 방법")):
        t.cell(0, j).text = h
    for i, row in enumerate(SEC_ROWS, 1):
        vals = (row[1], str(i), row[0], row[2], row[4], row[5])
        for j, v in enumerate(vals):
            t.cell(i, j).text = v
    for row in SEC_ROWS:
        sid, _, name, reqs, app, _, method, result, verdict = row
        d.add_paragraph(f"{sid}. {name}")
        r = d.add_table(rows=9, cols=4)
        r.cell(0, 0).text, r.cell(0, 1).text, r.cell(0, 2).text = "시험결과", app, "비고"
        r.cell(0, 3).text = "-" if app == "적용" else "적용 예외: 첨부3"
        for k, (label, content) in enumerate((("해당 요구사항", [reqs]), ("시험방법", method),
                                              ("확인결과", result), ("판정", [verdict]))):
            lab = r.cell(1 + 2 * k, 0).merge(r.cell(1 + 2 * k, 3))
            lab.text = label
            body = r.cell(2 + 2 * k, 0).merge(r.cell(2 + 2 * k, 3))
            body.text = content[0]
            for extra in content[1:]:
                body.add_paragraph(extra)
    d.save(path)


def build_case(dirpath, docs=("MDSR", "MDDR", "XXCS"), request=None):
    inp = Path(dirpath)
    inp.mkdir(parents=True, exist_ok=True)
    builders = {"MDSR": build_mdsr, "MDDR": build_mddr, "XXCS": build_xxcs}
    for name in docs:
        builders[name](inp / f"{name}.docx")
    (inp / "site_index.md").write_text("\n".join(index_lines(parse_case(inp))) + "\n", encoding="utf-8")
    if request:
        (inp / "request.txt").write_text(request, encoding="utf-8")
    return inp
