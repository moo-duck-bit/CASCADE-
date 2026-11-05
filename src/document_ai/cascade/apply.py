"""편집을 docx 사본에 칸 단위로 쓴다. 원본은 건드리지 않는다.

칸 하나가 문단 하나라서 여러 문단 칸(#pN)도 그 문단만 바꾼다(다른 문단 복제·옛 값 잔존이 생기지 않는다).
글이 있는 첫 런의 서식(rPr)과 문단 서식(pPr)은 유지한다.
새 Req는 앵커 Req 블록(표형 문서는 표, 문단형 문서는 번호·본문 문단)을 복제해 바로 뒤에 넣는다.
어느 문서가 어느 형인지는 도메인 팩의 layouts에서 읽는다(EC-SW: MDSR 표형, MDDR 문단형).
"""

import copy
import io
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from . import pack as pack_mod
from .cells import cell_paras, parse_document, present_docs, row_cells, split_site

RUN_TAGS = (qn("w:r"), qn("w:hyperlink"), qn("w:ins"), qn("w:sdt"), qn("w:smartTag"), qn("w:fldSimple"))


@dataclass
class ApplyResult:
    applied: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    out_dir: Path = None


def set_para_text(p, text):
    rpr = None
    for r in p._p.iter(qn("w:r")):
        if r.find(qn("w:t")) is not None and r.find(qn("w:rPr")) is not None:
            rpr = copy.deepcopy(r.find(qn("w:rPr")))
            break
    for child in list(p._p):
        if child.tag in RUN_TAGS:
            p._p.remove(child)
    run = p.add_run(text)
    if rpr is not None:
        run._r.insert(0, rpr)


def _set_cell_texts(cell, texts):
    paras = cell_paras(cell)
    first = paras[0]
    for extra in paras[1:]:
        extra._p.getparent().remove(extra._p)
    set_para_text(first, texts[0] if texts else "")
    prev = first
    for t in texts[1:]:
        new = copy.deepcopy(first._p)
        prev._p.addnext(new)
        prev = Paragraph(new, first._parent)
        set_para_text(prev, t)


def _field_groups(edits):
    """{칸 이름(#p 뗀 것): [글 …]} (#pN 순서대로)."""
    groups = defaultdict(list)
    for e in edits:
        name = e["field"]
        base, _, k = name.partition("#p")
        groups[base].append((int(k) if k.isdigit() else 0, e["new_text"]))
    return {b: [t for _, t in sorted(v)] for b, v in groups.items()}


def _insert_table(document, cells, anchor, item, edits, pack):
    anchor_cells = [c for c in cells if c.item == anchor]
    if not anchor_cells or not isinstance(anchor_cells[0].block, Table):
        raise KeyError(f"anchor table {anchor} not found")
    tbl = anchor_cells[0].block._tbl
    new_tbl = copy.deepcopy(tbl)
    after = tbl
    nxt = tbl.getnext()
    if nxt is not None and nxt.tag == qn("w:p") and not "".join(t.text or "" for t in nxt.iter(qn("w:t"))).strip():
        spacer = copy.deepcopy(nxt)
        tbl.addnext(spacer)
        after = spacer
    after.addnext(new_tbl)
    table = Table(new_tbl, anchor_cells[0].block._parent)
    groups = _field_groups(edits)
    rows = [row_cells(r) for r in table.rows]
    _set_cell_texts(rows[0][0], [groups.pop(pack.number_cell, [item])[0]])
    if len(rows[0]) > 1:
        _set_cell_texts(rows[0][1], groups.pop(pack.title_cell, [""]))
    for r in rows[1:]:
        label = " ".join(" ".join(t.text or "" for t in p._p.iter(qn("w:t"))) for p in cell_paras(r[0])).strip()
        for c in r[1:]:
            _set_cell_texts(c, groups.get(label, [""]))


def _insert_paragraphs(document, cells, anchor, item, edits, pack):
    anchor_cells = [c for c in cells if c.item == anchor]
    if not anchor_cells:
        raise KeyError(f"anchor {anchor} not found")
    body = pack.body_cell(anchor_cells[0].doc)
    num_p = anchor_cells[0].block
    design = [c.para for c in anchor_cells if c.base == body]
    last = design[-1] if design else num_p
    groups = _field_groups(edits)
    new_num = copy.deepcopy(num_p._p)
    last._p.addnext(new_num)
    prev = Paragraph(new_num, num_p._parent)
    set_para_text(prev, groups.get(pack.number_cell, [item])[0])
    proto = design[0] if design else num_p
    for t in groups.get(body, []):
        new = copy.deepcopy(proto._p)
        prev._p.addnext(new)
        prev = Paragraph(new, proto._parent)
        set_para_text(prev, t)


def apply_edits(input_dir, out_dir, edits, reader=None, pack=None):
    pack = pack_mod.load(pack)
    input_dir, out_dir = Path(input_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = ApplyResult(out_dir=out_dir)
    if pack.is_text:
        from .textdocs import apply_text_edits
        return apply_text_edits(input_dir, out_dir, edits, reader, res, split_site)
    by_doc = defaultdict(list)
    for e in edits:
        parts = split_site(e["site"])
        if not parts or parts[0] not in pack.docs:
            res.failed.append({"site": e["site"], "reason": "bad site"})
            continue
        by_doc[parts[0]].append(e)
    for name in present_docs(input_dir, pack):
        data = reader(input_dir / f"{name}.docx") if reader else (input_dir / f"{name}.docx").read_bytes()
        target = out_dir / f"{name}.docx"
        if not by_doc.get(name):
            target.write_bytes(data)
            continue
        document = Document(io.BytesIO(data))
        cells = parse_document(name, document, pack)
        site_map = {}
        for c in cells:
            site_map.setdefault(c.site, c)
        inserts = defaultdict(list)
        for e in by_doc[name]:
            if e["op"] == "insert" and e["site"] not in site_map:
                inserts[(e["item"], e.get("anchor"))].append(e)
                continue
            c = site_map.get(e["site"])
            if c is None:
                res.failed.append({"site": e["site"], "reason": "site not found"})
                continue
            set_para_text(c.para, e["new_text"])
            res.applied.append(e["site"])
        for (item, anchor), group in inserts.items():
            try:
                if not anchor:
                    raise KeyError("insert without anchor")
                insert = _insert_table if pack.is_table_doc(name) else _insert_paragraphs
                insert(document, cells, anchor, item, group, pack)
                res.applied += [e["site"] for e in group]
            except (KeyError, IndexError) as err:
                res.failed += [{"site": e["site"], "reason": str(err)} for e in group]
        document.save(str(target))
    for name in by_doc:
        if name not in present_docs(input_dir, pack):
            res.failed += [{"site": e["site"], "reason": f"{name} not in input"} for e in by_doc[name]]
    return res
