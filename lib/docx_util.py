"""python-docx 보조 함수. 본문 블록을 문서 순서대로 돌고(내용 컨트롤 w:sdt 안쪽 포함), 서식을 지키며 글을 바꾼다."""

import copy
import re

from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

W_P, W_TBL, W_SDT, W_SDTCONTENT = qn("w:p"), qn("w:tbl"), qn("w:sdt"), qn("w:sdtContent")


def _iter_children(parent_el, parent):
    for child in parent_el.iterchildren():
        if child.tag == W_P:
            yield Paragraph(child, parent)
        elif child.tag == W_TBL:
            yield Table(child, parent)
        elif child.tag == W_SDT:
            content = child.find(W_SDTCONTENT)
            if content is not None:
                yield from _iter_children(content, parent)


def iter_blocks(doc):
    yield from _iter_children(doc.element.body, doc)


def cell_paragraphs(cell):
    """셀 안 문단(내용 컨트롤 안 문단 포함)."""
    return [b for b in _iter_children(cell._tc, cell) if isinstance(b, Paragraph)]


def para_text(p):
    """런·하이퍼링크·내용 컨트롤 안 텍스트까지 합친 문단 텍스트."""
    return "".join(t.text or "" for t in p._p.iter(qn("w:t")))


def cell_text(cell):
    return "\n".join(para_text(p) for p in cell_paragraphs(cell))


def first_text(cell):
    """셀 첫 문단의 텍스트(앞뒤 공백 제거)."""
    return para_text(cell_paragraphs(cell)[0]).strip()


def unique_cells(row):
    """가로 병합 셀을 한 번만 돌려준다."""
    seen, out = set(), []
    for c in row.cells:
        if id(c._tc) in seen:
            continue
        seen.add(id(c._tc))
        out.append(c)
    return out


def set_para_text(p, text):
    """문단 글을 바꾼다. 글이 있는 첫 런의 서식(rPr)과 문단 서식(pPr)은 그대로 둔다."""
    rpr = None
    for r in p._p.findall(".//" + qn("w:r")):
        if r.find(qn("w:rPr")) is not None and r.find(qn("w:t")) is not None:
            rpr = copy.deepcopy(r.find(qn("w:rPr")))
            break
    for child in list(p._p):
        if child.tag in (qn("w:r"), qn("w:hyperlink"), qn("w:ins"), qn("w:sdt")):
            p._p.remove(child)
    run = p.add_run(text)
    if rpr is not None:
        run._r.insert(0, rpr)


def insert_para_after(p, text, proto=None):
    """p 뒤에 새 문단을 넣는다. proto가 있으면 그 문단의 서식을 복사한다."""
    new = copy.deepcopy((proto or p)._p)
    p._p.addnext(new)
    new_p = Paragraph(new, p._parent)
    set_para_text(new_p, text)
    return new_p


def set_cell_paras(cell, texts):
    paras = cell_paragraphs(cell)
    first = paras[0]
    for extra in paras[1:]:
        extra._p.getparent().remove(extra._p)
    set_para_text(first, texts[0])
    prev = first
    for t in texts[1:]:
        prev = insert_para_after(prev, t)


def ensure_rows(table, n):
    """행이 n개보다 적으면 마지막 행을 복제한다."""
    while len(table.rows) < n:
        table._tbl.append(copy.deepcopy(table.rows[-1]._tr))


def remove_block(block):
    el = block._p if isinstance(block, Paragraph) else block._tbl
    el.getparent().remove(el)


def find_para(doc, text, start_after=None):
    """글이 text인 첫 문단. start_after가 있으면 그 문단 뒤에서 찾는다."""
    seen = start_after is None
    for b in iter_blocks(doc):
        if not seen:
            seen = isinstance(b, Paragraph) and b._p is start_after._p
            continue
        if isinstance(b, Paragraph) and para_text(b).strip() == text:
            return b
    raise KeyError(text)


def drop_unreferenced_images(doc):
    """본문에서 참조하지 않는 이미지 파트를 지우고 지운 개수를 돌려준다."""
    part = doc.part
    used = set(re.findall(r'r:(?:embed|link|id)="([^"]+)"', part._element.xml))
    dropped = 0
    for rid, rel in list(part.rels.items()):
        if "image" in rel.reltype and rid not in used:
            part.drop_rel(rid)
            dropped += 1
    return dropped
