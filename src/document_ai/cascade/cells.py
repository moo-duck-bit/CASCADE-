"""docx 문서 → 칸 목록. 칸 주소 "[문서 | 항목 | 칸]"은 케이스 input/site_index.md와 같은 규칙을 따른다.

문서 이름, 항목 번호 형식, 문서별 표·문단 규칙은 도메인 팩(pack.py, domain_packs/<팩>/pack.yaml)에서 읽는다.

- 여러 문단 칸은 #p1, #p2 …, 여러 열 칸은 #c1, #c2 … 를 붙이고 빈 문단은 건너뛴다.
- 칸 하나가 문단 하나다. Cell.para로 그 문단에 바로 쓸 수 있다(apply.py).
- 내용 컨트롤(w:sdt) 안쪽 문단과 표도 문서 순서대로 읽는다.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from . import pack as pack_mod

TOC_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(\D.*?)\s*\d+$")

W_P, W_TBL, W_SDT, W_SDTCONTENT, W_T = qn("w:p"), qn("w:tbl"), qn("w:sdt"), qn("w:sdtContent"), qn("w:t")


@dataclass(eq=False)
class Cell:
    doc: str
    item: str
    cell: str
    text: str
    para: object = field(default=None, repr=False)   # python-docx Paragraph
    block: object = field(default=None, repr=False)  # 항목을 담은 표(MDSR Req) 또는 번호 문단(MDDR Req)

    @property
    def site(self):
        return f"[{self.doc} | {self.item} | {self.cell}]"

    @property
    def base(self):
        """#p/#c 를 뗀 칸 이름."""
        return self.cell.split("#")[0]


def site_of(doc, item, cell):
    return f"[{doc} | {item} | {cell}]"


SITE_RE = re.compile(r"^\[([^|\[\]]+?) \| (.+?) \| (.+?)\]$")


def split_site(site):
    m = SITE_RE.match(site.strip())
    return m.groups() if m else None


# docx 순회

def _children(parent_el, parent):
    for child in parent_el.iterchildren():
        if child.tag == W_P:
            yield Paragraph(child, parent)
        elif child.tag == W_TBL:
            yield Table(child, parent)
        elif child.tag == W_SDT:
            content = child.find(W_SDTCONTENT)
            if content is not None:
                yield from _children(content, parent)


def blocks(doc):
    yield from _children(doc.element.body, doc)


def para_text(p):
    return "".join(t.text or "" for t in p._p.iter(W_T))


def cell_paras(cell):
    return [b for b in _children(cell._tc, cell) if isinstance(b, Paragraph)]


def row_cells(row):
    """가로 병합 셀은 한 번만."""
    seen, out = set(), []
    for c in row.cells:
        if id(c._tc) not in seen:
            seen.add(id(c._tc))
            out.append(c)
    return out


def _joined(cell):
    return " ".join(para_text(p) for p in cell_paras(cell)).strip()


class _Walker:
    def __init__(self, doc_name, document, pack):
        self.name = doc_name
        self.doc = document
        self.pack = pack
        self.lay = pack.layout(doc_name)
        self.out = []

    def emit(self, item, cell, paras, block=None):
        paras = [p for p in paras if para_text(p).strip()]
        names = [cell] if len(paras) == 1 else [f"{cell}#p{k}" for k in range(1, len(paras) + 1)]
        for name, p in zip(names, paras):
            self.out.append(Cell(self.name, item, name, para_text(p), p, block))

    def walk(self):
        toc = []
        for b in blocks(self.doc):
            if isinstance(b, Paragraph):
                m = TOC_RE.match(para_text(b).strip())
                if m:
                    toc.append((m.group(1), m.group(2).strip()))
        toc_titles = {t for _, t in toc}
        heading, req_item, req_block, sec_item = "표지", None, None, None
        body, toc_pos, in_toc = [], 0, True
        body_cell = self.pack.body_cell(self.name)
        sec_cell, angle_cell = self.lay.get("sec_heading"), self.lay.get("angle_heading")

        def flush():
            nonlocal body
            if body:
                if req_item and body_cell:
                    self.emit(req_item, body_cell, body, req_block)
                else:
                    self.emit(heading, "본문", body)
            body = []

        for b in blocks(self.doc):
            if isinstance(b, Table):
                flush()
                self.table(b, heading, sec_item)
                continue
            text = para_text(b).strip()
            if not text:
                continue
            if in_toc and TOC_RE.match(text):
                body.append(b)
                continue
            matched = None
            if text in toc_titles:  # 목차 순서대로 앞으로 2개까지 맞춘다
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
            rid = self.pack.match_req(text)
            if body_cell and rid:
                flush()
                req_item, req_block = rid, b
                self.emit(rid, self.pack.number_cell, [b], b)
                continue
            m = self.pack.sec_re.match(text)
            if sec_cell and m and "." in text[:8]:
                flush()
                sec_item = m.group(1)
                self.emit(sec_item, sec_cell, [b])
                continue
            if angle_cell and text.startswith("<"):
                flush()
                heading = text.strip("<>")
                self.emit(heading, angle_cell, [b])
                continue
            body.append(b)
        flush()
        return self.out

    def table(self, table, heading, sec_item):
        rows = [row_cells(r) for r in table.rows]
        if not rows:
            return
        first = [_joined(c) for c in rows[0]]
        rid = self.pack.match_req(first[0]) if first else None
        if self.lay.get("item_table") and rid:
            self.emit(rid, self.pack.number_cell, cell_paras(rows[0][0]), table)
            if len(rows[0]) > 1:
                self.emit(rid, self.pack.title_cell, cell_paras(rows[0][1]), table)
            for r in rows[1:]:
                label = _joined(r[0])
                for j, c in enumerate(r[1:], 1):
                    self.emit(rid, label if len(r) == 2 else f"{label}#c{j}", cell_paras(c), table)
            return
        heads = [" ".join(_joined(c) for c in r) for r in rows[:2]]
        for spec in self.lay.get("id_tables", []):
            # 항목 번호 열이 있는 표(MDSR 6절 연결 표, XXCS 체크리스트): 행마다 지정한 열을 칸으로
            if any(spec["marker"] in h for h in heads):
                col = spec["id_col"]
                for r in rows:
                    if len(r) >= spec["min_cols"] and self.pack.sec_re.match(_joined(r[col])):
                        sid = _joined(r[col])
                        for j, name in spec["cols"].items():
                            self.emit(sid, name, cell_paras(r[int(j)]), table)
                return
        rt = self.lay.get("result_table")
        if rt and first and first[0] == rt["first"] and sec_item:
            r0 = rows[0]
            if len(r0) > 1:
                self.emit(sec_item, rt["status"], cell_paras(r0[1]), table)
            labels = [_joined(c) for c in r0]
            note = rt.get("note")
            if note and note in labels and labels.index(note) + 1 < len(r0):
                self.emit(sec_item, note, cell_paras(r0[labels.index(note) + 1]), table)
            label = None
            for r in rows[1:]:
                text = _joined(r[0])
                if text in rt["labels"]:
                    label = text
                elif label:
                    self.emit(sec_item, label, cell_paras(r[0]), table)
                    label = None
            return
        for ri, r in enumerate(rows):
            label = _joined(r[0]) or f"r{ri}"
            for j, c in enumerate(r[1:], 1):
                self.emit(heading, f"{label}#c{j}" if len(r) > 2 else label, cell_paras(c), table)


def parse_document(doc_name, document, pack=None):
    return _Walker(doc_name, document, pack_mod.load(pack)).walk()


def parse_doc(doc_name, path, reader=None, pack=None):
    """reader: 가드의 read_bytes 같은 함수. 주면 그 길로 읽는다."""
    import io
    data = reader(path) if reader else Path(path).read_bytes()
    return parse_document(doc_name, Document(io.BytesIO(data)), pack)


def present_docs(input_dir, pack=None):
    pk = pack_mod.load(pack)
    if pk.is_text:
        from .textdocs import doc_names
        return doc_names(input_dir)
    return [n for n in pk.docs if (Path(input_dir) / f"{n}.docx").exists()]


def parse_case(input_dir, reader=None, pack=None):
    if pack_mod.load(pack).is_text:
        from .textdocs import parse_text_case
        return parse_text_case(input_dir, reader)
    out = []
    for name in present_docs(input_dir, pack):
        out += parse_doc(name, Path(input_dir) / f"{name}.docx", reader, pack)
    return out


def index_lines(cells):
    return [f"{c.site} {' '.join(c.text.split())}" for c in cells]
