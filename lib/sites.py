"""렌더된 문서 3종의 모든 칸에 위치 이름 "[문서 | 항목 | 칸]"을 붙인다.

여러 문단 칸은 #p1, #p2 … 를 붙이고 빈 칸은 건너뛴다. render, make_cases, check_cases가 같은 이름을 쓴다.
"""

import re

from docx import Document
from docx.table import Table

from docx_util import cell_paragraphs, iter_blocks, para_text, unique_cells

REQ_RE = re.compile(r"^Req\.\s*(\d+)$")
SEC_RE = re.compile(r"^((?:IA|UC|SI|DC|TRE|RA)-\d{2})\b")
TOC_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(\D.*?)\s*\d+$")
XXCS_LABELS = ("해당 요구사항", "시험방법", "확인결과", "판정")
DOC_NAMES = ("MDSR", "MDDR", "XXCS")


def site(doc, item, cell):
    return f"[{doc} | {item} | {cell}]"


def cell_names(cell, n):
    """문단 n개짜리 칸의 이름. 하나면 그대로, 여럿이면 #p1, #p2 …"""
    return [cell] if n == 1 else [f"{cell}#p{k}" for k in range(1, n + 1)]


def norm_req(text):
    m = REQ_RE.match(text.strip())
    return f"Req. {m.group(1)}" if m else None


def _toc(doc):
    out = []
    for b in iter_blocks(doc):
        if isinstance(b, Table):
            continue
        m = TOC_RE.match(para_text(b).strip())
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out


def _emit(out, doc, item, cell, paras):
    paras = [p for p in paras if p.strip()]
    for name, text in zip(cell_names(cell, len(paras)), paras):
        out.append({"site": site(doc, item, name), "doc": doc, "item": item, "cell": name, "text": text})


def _cell_paras(cell):
    return [para_text(p) for p in cell_paragraphs(cell)]


def _joined(cell):
    return " ".join(_cell_paras(cell)).strip()


def walk(doc_name, path):
    doc = Document(str(path))
    toc = _toc(doc)
    toc_texts = {t for _, t in toc}
    out = []
    heading = "표지"
    body_paras = []
    req_item = None
    sec_item = None
    toc_pos = 0
    in_toc = True

    def flush():
        nonlocal body_paras
        if body_paras:
            if req_item and doc_name == "MDDR":
                _emit(out, doc_name, req_item, "설계", body_paras)
            else:
                _emit(out, doc_name, heading, "본문", body_paras)
        body_paras = []

    for b in iter_blocks(doc):
        if isinstance(b, Table):
            flush()
            _walk_table(out, doc_name, b, heading, sec_item)
            continue
        text = para_text(b).strip()
        if not text:
            continue
        if TOC_RE.match(text) and in_toc:
            body_paras.append(text)
            continue
        # 목차 다음 제목: 앞으로 2개까지 내다보며 맞춘다(빠진 절, 같은 이름 하위 문단 대비).
        matched = None
        if text in toc_texts:
            for j in range(toc_pos, min(toc_pos + 2, len(toc))):
                if toc[j][1] == text:
                    matched = j
                    break
        if matched is not None:
            flush()
            in_toc = False
            toc_pos = matched + 1
            heading = f"{toc[matched][0]} {toc[matched][1]}"
            req_item = None
            continue
        rid = norm_req(text)
        if doc_name == "MDDR" and rid:
            flush()
            req_item = rid
            _emit(out, doc_name, rid, "번호", [text])
            continue
        m = SEC_RE.match(text)
        if doc_name == "XXCS" and m and "." in text[:8]:
            flush()
            sec_item = m.group(1)
            _emit(out, doc_name, sec_item, "제목", [text])
            continue
        if doc_name == "XXCS" and text.startswith("<"):
            flush()
            heading = text.strip("<>")
            _emit(out, doc_name, heading, "제목", [text])
            continue
        body_paras.append(text)
    flush()
    return out


def _walk_table(out, doc, table, heading, sec_item):
    rows = [unique_cells(r) for r in table.rows]
    if not rows:
        return
    first = [_joined(c) for c in rows[0]]
    rid = norm_req(first[0]) if first else None
    if doc == "MDSR" and rid:
        _emit(out, doc, rid, "번호", _cell_paras(rows[0][0]))
        if len(rows[0]) > 1:
            _emit(out, doc, rid, "제목", _cell_paras(rows[0][1]))
        for r in rows[1:]:
            label = _joined(r[0])
            for j, c in enumerate(r[1:], 1):
                _emit(out, doc, rid, label if len(r) == 2 else f"{label}#c{j}", _cell_paras(c))
        return
    header_rows = [" ".join(" ".join(_cell_paras(c)) for c in r) for r in rows[:2]]
    if doc == "MDSR" and any("사이버보안 요구사항 번호" in h for h in header_rows):
        names = ["번호", "항목", "요구사항", "소프트웨어 기능 요구사항 번호"]
        for r in rows:
            sid = _joined(r[0])
            if SEC_RE.match(sid) and len(r) >= 4:
                for j in range(1, 4):
                    _emit(out, doc, sid, names[j], _cell_paras(r[j]))
        return
    if doc == "XXCS" and any("해당기기 적용여부" in h for h in header_rows):
        for r in rows:
            if len(r) >= 6 and SEC_RE.match(_joined(r[2])):
                sid = _joined(r[2])
                _emit(out, doc, sid, "체크리스트 적용 여부", _cell_paras(r[4]))
                _emit(out, doc, sid, "체크리스트 입증 방법", _cell_paras(r[5]))
        return
    if doc == "XXCS" and first and first[0] == "시험결과" and sec_item:
        r0 = rows[0]
        if len(r0) > 1:
            _emit(out, doc, sec_item, "적용 여부", _cell_paras(r0[1]))
        labels = [_joined(c) for c in r0]
        if "비고" in labels and labels.index("비고") + 1 < len(r0):
            _emit(out, doc, sec_item, "비고", _cell_paras(r0[labels.index("비고") + 1]))
        label = None
        for r in rows[1:]:
            text = _joined(r[0])
            if text in XXCS_LABELS:
                label = text
            elif label:
                _emit(out, doc, sec_item, label, _cell_paras(r[0]))
                label = None
        return
    item = "표지" if doc == "XXCS" and heading == "표지" else heading
    for ri, r in enumerate(rows):
        label = _joined(r[0]) or f"r{ri}"
        for j, c in enumerate(r[1:], 1):
            _emit(out, doc, item, f"{label}#c{j}" if len(r) > 2 else label, _cell_paras(c))


def walk_all(docs_dir):
    out = []
    for name in DOC_NAMES:
        out += walk(name, docs_dir / f"{name}.docx")
    return out


def site_index_md(sites):
    lines = [f"{s['site']} {' '.join(s['text'].split())}" for s in sites]
    return "\n".join(lines) + "\n"
