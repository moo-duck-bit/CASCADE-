"""content.json과 facts/links로 MDSR·MDDR·XXCS를 렌더하고 모든 칸의 위치와 치환 기록을 남긴다.

{{F_ID}} 토큰은 사실의 표기(variants)를 돌아가며 넣는다. 출력: <docs>/*.docx, sites.json, site_index.md
"""

import copy
import re
from collections import defaultdict

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from datagen import TEMPLATES, TOKEN_RE, read_json, write_json
from docx_util import (cell_paragraphs, drop_unreferenced_images, ensure_rows, find_para, first_text, insert_para_after,
                       iter_blocks, para_text, remove_block, set_cell_paras, set_para_text, unique_cells)
from sites import REQ_RE, SEC_RE, cell_names, site, site_index_md, walk_all

# 숫자를 읽었을 때 받침이 있는지(영 일 이 삼 사 오 육 칠 팔 구).
DIGIT_HAS_FINAL = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True,
                   "8": True, "9": False}
JOSA_PAIRS = [("을", "를"), ("이", "가"), ("은", "는"), ("과", "와"), ("으로", "로")]
JOSA_RE = re.compile(r"^(으로|로|을|를|이|가|은|는|과|와)(?![가-힣])")


def req_num(rid):
    return int(rid.split()[1])


def has_final(word):
    """마지막 글자에 받침이 있는지. 숫자는 읽는 소리로 보고 영문은 받침 없음으로 본다. 모르면 None."""
    ch = word.rstrip()[-1:]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return DIGIT_HAS_FINAL[ch]
    return False if ch.isalpha() else None


def fit_josa(surface, josa):
    fin = has_final(surface)
    if fin is None:
        return josa
    ends_rieul = "가" <= surface[-1:] <= "힣" and (ord(surface[-1]) - 0xAC00) % 28 == 8
    for a, b in JOSA_PAIRS:
        if josa in (a, b):
            if a == "으로":
                return "로" if (not fin or ends_rieul) else "으로"
            return a if fin else b
    return josa


class Substituter:
    """{{F_ID}}를 variants로 돌아가며 바꾸고(사실마다 카운터 따로) 치환마다 위치를 기록한다."""

    def __init__(self, facts):
        self.facts = facts
        self.count = defaultdict(int)
        self.records = []

    def __call__(self, text, doc, item, cell):
        out, pos = [], 0
        for m in TOKEN_RE.finditer(text):
            fid = m.group(1)
            variants = self.facts[fid]["variants"]
            surface = variants[self.count[fid] % len(variants)]
            self.count[fid] += 1
            self.records.append({"fact": fid, "site": site(doc, item, cell), "item": item, "surface": surface})
            out.append(text[pos:m.start()])
            out.append(surface)
            pos = m.end()
            j = JOSA_RE.match(text[pos:])
            if j:
                out.append(fit_josa(surface, j.group(1)))
                pos += len(j.group(1))
        out.append(text[pos:])
        return "".join(out)

    def cell(self, texts, doc, item, cell):
        """한 칸의 문단들을 치환한다. 문단이 여럿이면 칸 이름에 #p1, #p2 … 를 붙인다."""
        return [self(t, doc, item, name) for t, name in zip(texts, cell_names(cell, len(texts)))]


def table_label(table):
    return first_text(unique_cells(table.rows[0])[0])


def link_text(sec, links, app):
    if app[sec]["status"] == "N/A":
        return "해당 없음"
    reqs = sorted((link["req"] for link in links if link["sec"] == sec), key=req_num)
    return ", ".join(reqs) or "해당 없음"


def fill_identity(table, profile):
    values = {"제품명": profile["name"], "품목명": profile["name"], "모델명": profile["model_name"],
              "제조자": profile["maker"]}
    for r in table.rows:
        cells = unique_cells(r)
        label = first_text(cells[0])
        if label in values and len(cells) > 1:
            set_cell_paras(cells[1], [values[label]])


def add_missing_req_tables(doc, groups):
    """템플릿에 없는 Req 표를 같은 구성요소의 마지막 Req 표(와 뒤의 빈 문단)를 복제해 만든다. 번호 칸만 채운다."""
    tables = {}
    for b in iter_blocks(doc):
        if isinstance(b, Table):
            m = REQ_RE.match(table_label(b))
            if m:
                tables[f"Req. {m.group(1)}"] = b
    for group in groups:
        last = tables[max((r for r in group if r in tables), key=req_num)]
        for rid in group:
            if rid in tables:
                continue
            spacer = last._tbl.getnext()
            new_tbl = copy.deepcopy(last._tbl)
            anchor = spacer if spacer is not None and spacer.tag == qn("w:p") else last._tbl
            anchor.addnext(new_tbl)
            if anchor is spacer:
                new_tbl.addnext(copy.deepcopy(spacer))
            last = Table(new_tbl, last._parent)
            set_cell_paras(unique_cells(last.rows[0])[0], [rid])
            tables[rid] = last


def render_mdsr(content, reqs, links, groups, profile, sub):
    doc = Document(str(TEMPLATES / "template_mdsr.docx"))
    app = content["applicability"]
    keep = {rid for group in groups for rid in group}
    add_missing_req_tables(doc, groups)
    for b in list(iter_blocks(doc)):
        if isinstance(b, Paragraph):
            t = para_text(b)
            if "Google Cloud Platform" in t or "Cloud Run" in t:
                set_para_text(b, "서버는 클라우드 기반 컨테이너 환경에서 구동되며, 트래픽에 따라 확장 운영된다.")
            continue
        first = table_label(b)
        if first == "제품명":
            fill_identity(b, profile)
        m = REQ_RE.match(first)
        if m:
            rid = f"Req. {m.group(1)}"
            if rid not in keep:
                remove_block(b)
                continue
            body = content["mdsr"][rid]
            rows = [unique_cells(r) for r in b.rows]
            set_cell_paras(rows[0][0], [rid])
            set_cell_paras(rows[0][1], [reqs[rid]["title"]])
            for r in rows[1:]:
                label = first_text(r[0])
                if label in ("설명", "목적"):
                    set_cell_paras(r[1], [sub(body[label], "MDSR", rid, label)])
                elif label == "기준":
                    set_cell_paras(r[1], sub.cell(body["기준"], "MDSR", rid, "기준"))
        elif any("사이버보안 요구사항 번호" in para_text(p)
                 for row in b.rows[:2] for cell in unique_cells(row) for p in cell_paragraphs(cell)):
            for r in b.rows:
                cells = unique_cells(r)
                sid = first_text(cells[0])
                if SEC_RE.match(sid) and len(cells) >= 4:
                    set_cell_paras(cells[3], [link_text(sid, links, app)])
    # 대시보드 Req 표를 지웠으니 대시보드 소제목 아래에 범위 문장을 둔다.
    body_proto = find_para(doc, "본 문서의 1.3에서 식별하고 있는 소프트웨어의 요구사항에 한한다.")
    dash = [b for b in iter_blocks(doc) if isinstance(b, Paragraph) and para_text(b).strip() == "대시보드"]
    if dash:
        insert_para_after(dash[-1], "대시보드 기능 요구사항은 본 문서의 범위에서 별도로 정의하지 않는다.", body_proto)
    # 3.4 일반적 제한 아래에 보안 개요 문단
    prev = find_para(doc, "일반적 제한", find_para(doc, "제품 개요"))
    for t in sub.cell(content["mdsr34"], "MDSR", "3.4 일반적 제한", "본문"):
        prev = insert_para_after(prev, t, body_proto)
    return doc


def render_mddr(content, groups, profile, sub):
    doc = Document(str(TEMPLATES / "template_mddr.docx"))
    replace = profile["mddr_replace"]
    for b in iter_blocks(doc):
        if isinstance(b, Paragraph) and para_text(b).strip() in replace:
            set_para_text(b, replace[para_text(b).strip()])
        elif isinstance(b, Table) and table_label(b) == "제품명":
            fill_identity(b, profile)
    heads = {}
    for b in iter_blocks(doc):
        if isinstance(b, Paragraph):
            m = REQ_RE.match(para_text(b).strip())
            if m:
                heads[f"Req. {m.group(1)}"] = b
    # 템플릿에 없는 앱·서버 Req 제목을 넣고, 대시보드 Req 제목은 범위 문장 하나로 바꾼다.
    for group in groups:
        last = max((r for r in group if r in heads), key=req_num)
        for rid in group:
            if rid not in heads:
                heads[rid] = insert_para_after(heads[last], rid)
                last = rid
    all_reqs = [rid for group in groups for rid in group]
    body_proto = find_para(doc, "1.3에 식별된 제품에 적용된다.")
    dash_heads = sorted((r for r in heads if r not in all_reqs), key=req_num)
    if dash_heads:
        insert_para_after(heads[dash_heads[0]], "대시보드 설계 사항은 본 문서의 범위에서 별도로 정의하지 않는다.", body_proto)
    for rid in dash_heads:
        remove_block(heads[rid])
    for rid in all_reqs:
        prev = heads[rid]
        set_para_text(prev, rid)
        for t in sub.cell(content["mddr"][rid], "MDDR", rid, "설계"):
            prev = insert_para_after(prev, "• " + t, body_proto)
    return doc


def render_xxcs(content, links, rules, profile, sub):
    doc = Document(str(TEMPLATES / "template_xxcs_skeleton.docx"))
    app = content["applicability"]
    rules = {r["id"]: r for r in rules}
    tables = [b for b in iter_blocks(doc) if isinstance(b, Table)]
    fill_identity(tables[0], profile)
    for r in tables[1].rows:
        cells = unique_cells(r)
        if len(cells) >= 6:
            sid = first_text(cells[2])
            if SEC_RE.match(sid):
                set_cell_paras(cells[4], [app[sid]["status"]])
                if app[sid]["status"] == "N/A":
                    set_cell_paras(cells[5], ["해당 없음"])
    cur = None
    for b in iter_blocks(doc):
        if isinstance(b, Paragraph):
            m = SEC_RE.match(para_text(b).strip())
            if m:
                cur = m.group(1)
            continue
        if cur is None or table_label(b) != "시험결과":
            continue
        sid, cur = cur, None
        status = app[sid]["status"]
        ensure_rows(b, 9)
        rows = [unique_cells(r) for r in b.rows]
        r0 = rows[0]
        note_idx = next(i for i, cell in enumerate(r0) if first_text(cell) == "비고")
        set_cell_paras(r0[1], [status])
        for mid in r0[2:note_idx]:
            set_cell_paras(mid, ["만족" if status == "적용" else "-"])
        if status == "N/A":
            rule = rules.get(app[sid]["rule"]) if app[sid]["rule"] else None
            note = f"적용 예외: {rule['source']}" if rule else "본 제품 범위에서 해당 기능을 제공하지 않으므로 미적용"
            body = {"해당 요구사항": ["해당 없음"], "시험방법": ["해당 없음"], "확인결과": ["해당 없음"], "판정": ["해당 없음"]}
        else:
            generated = content["xxcs"][sid]
            note = generated["비고"] or "-"
            body = {
                "해당 요구사항": [link_text(sid, links, app)],
                "시험방법": generated["시험방법"],
                "확인결과": generated["확인결과"],
                "판정": ["만족"],
            }
        set_cell_paras(r0[note_idx + 1], [sub(note, "XXCS", sid, "비고")])
        # 1행부터 라벨 행과 내용 행이 번갈아 온다.
        for k, (label, texts) in enumerate(body.items()):
            set_cell_paras(rows[1 + 2 * k][0], [label])
            set_cell_paras(rows[2 + 2 * k][0], sub.cell(texts, "XXCS", sid, label))
    return doc


def main(product_dir, docs_dir, app_reqs, server_reqs, profile):
    facts = read_json(product_dir / "facts.json")
    links = read_json(product_dir / "links.json")
    content = read_json(product_dir / "content.json")
    groups = (app_reqs, server_reqs)
    docs_dir.mkdir(exist_ok=True)
    sub = Substituter(facts["facts"])
    render_mdsr(content, facts["reqs"], links["links"], groups, profile, sub).save(str(docs_dir / "MDSR.docx"))
    render_mddr(content, groups, profile, sub).save(str(docs_dir / "MDDR.docx"))
    xxcs = render_xxcs(content, links["links"], links["rules"], profile, sub)
    dropped = drop_unreferenced_images(xxcs)
    xxcs.save(str(docs_dir / "XXCS.docx"))

    sites = walk_all(docs_dir)
    by_site = {s["site"]: s for s in sites}
    for s in sites:
        s["facts"] = []
    missing = []
    for rec in sub.records:
        s = by_site.get(rec["site"])
        if s is None or rec["surface"] not in s["text"]:
            missing.append(rec)
        else:
            s["facts"].append({"fact": rec["fact"], "surface": rec["surface"]})
    write_json(docs_dir / "sites.json", {"substitutions": sub.records, "sites": sites})
    (docs_dir / "site_index.md").write_text(site_index_md(sites), encoding="utf-8")
    print(f"rendered 3 docs; sites={len(sites)} substitutions={len(sub.records)} "
          f"unmatched_substitutions={len(missing)} dropped_unreferenced_images={dropped}")
    for m in missing[:10]:
        print("  UNMATCHED", m)
    if missing:
        raise SystemExit(1)
