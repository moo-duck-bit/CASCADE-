"""범용 그래프층: 도메인 팩의 연결 설정 없이 문서에서 엣지를 추론한다(graph_mode = generic | generic_rules).

- 식별자 형식 추론: 여러 문서에 반복되는 '영문 접두어 + 구분자(. 또는 -) + 숫자' 토큰을 접두어별로 모으고, 모양(대소문자,
  구분자, 숫자 자리 수)이 같은 접두어를 한 형식으로 묶는다. 형식은 2개 이상 문서에 나오고, 서로 다른 번호가 3개 이상이며,
  번호 하나 이상이 어떤 문서의 항목 이름일 때만 등록한다(번호는 칸 글과 항목 이름에서 모은다).
  한 문서에만 나오는 형식도 항목 이름으로 3개 이상 쓰이면 등록한다. 항목 이름으로 쓰이지 않는 형식은 ref_only로 두고
  연결 열의 대상(다른 문서에 없는 Req를 가리키는 XXCS 단독 케이스)으로만 쓴다.
- 연결 열 자동 감지: (문서, 칸 이름)이 같은 칸 묶음에서 다수(기본 0.6 이상)가 자기 항목이 아닌 다른 항목 번호를 담으면 연결 열.
  연결 열이 가리키는 형식을 Req 형식, 연결 열이 있는 행의 항목 형식을 연결 항목 형식으로 본다.
- 같은 번호 엣지와 값 엣지는 CellGraph가 추론된 형식으로 그대로 만든다.
- 유사도 엣지(LEDGER식): 출발 칸과 다른 문서 칸의 임베딩 코사인이 기준(기본 0.7) 이상이고 다른 엣지가 없을 때만.
문서를 칸으로 자르는 규칙과 칸 주소는 출력 형식이라 팩(layouts)에서 그대로 온다. generic은 규칙 표·판단 칸·코퍼스 같은
도메인층을 쓰지 않고, generic_rules는 도메인층(규칙 표, 판단 칸, 검색 코퍼스)을 더한다.
"""

import math
import re
from collections import Counter, defaultdict

from . import pack as pack_mod

TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Za-z]{0,5})(\.\s?|-)(\d{1,4})(?![\d])(?!\.\d)(?!-[A-Za-z0-9])")


def _shape(prefix, sep, lengths):
    case = "upper" if prefix.isupper() else "title"
    return case, sep.strip() or sep, (lengths.pop() if len(lengths) == 1 else "var")


def infer_id_formats(cells, min_docs=2, min_ids=3):
    per_prefix = defaultdict(lambda: {"ids": set(), "docs": set(), "lens": set(), "seps": Counter()})
    for c in cells:
        # 칸 글과 항목 이름(표의 번호 열처럼 칸으로 나오지 않는 번호) 둘 다 본다
        for m in list(TOKEN.finditer(c.text)) + list(TOKEN.finditer(c.item)):
            p, sep, num = m.groups()
            d = per_prefix[p]
            d["ids"].add(num if len(num) > 1 and num.startswith("0") else str(int(num)))
            d["docs"].add(c.doc)
            d["lens"].add(len(num))
            d["seps"][sep] += 1
    items = {c.item for c in cells}
    families = defaultdict(list)
    for p, d in per_prefix.items():
        sep = d["seps"].most_common(1)[0][0]
        families[_shape(p, sep, set(d["lens"]) if len(d["lens"]) == 1 else {0, 1})].append(p)
    out = []
    for (case, sep, ln), prefixes in sorted(families.items(), key=lambda kv: str(kv[0])):
        prefixes = sorted(prefixes)
        docs = set().union(*(per_prefix[p]["docs"] for p in prefixes))
        sep_surface = Counter()
        for p in prefixes:
            sep_surface.update(per_prefix[p]["seps"])
        surf = sep_surface.most_common(1)[0][0]
        ids = sorted({f"{p}{surf}{i}" for p in prefixes for i in per_prefix[p]["ids"]})
        alt = "|".join(map(re.escape, prefixes))
        sep_re = r"\.\s*" if sep == "." else re.escape(sep)
        num_re = r"\d+" if ln == "var" else rf"\d{{{ln}}}"
        core = rf"(?:{alt}){sep_re}{num_re}"
        full = re.compile(rf"^{core}$")
        n_items = sum(1 for i in items if full.match(i))
        # 여러 문서에 나오거나, 한 문서라도 항목 이름으로 3개 이상 쓰인 형식(연결 항목처럼 한 문서에만 있는 번호)
        ref_only = not n_items
        if len(ids) < min_ids or (not ref_only and len(docs) < min_docs and n_items < min_ids):
            continue
        out.append({"ref_only": ref_only, "name": f"F{len(out) + 1}", "prefixes": prefixes, "sep": surf, "docs": sorted(docs),
                    "ids": ids, "example": ids[0], "core": core, "num_re": num_re, "alt": alt, "sep_re": sep_re,
                    "single": len(prefixes) == 1})
    return out


def _refs(text, fmt):
    return re.findall(fmt["core"], text)


def detect_link_columns(cells, fmts, threshold=0.6, min_cells=3):
    groups = defaultdict(list)
    for c in cells:
        groups[(c.doc, c.base)].append(c)
    item_doc = defaultdict(set)
    for c in cells:
        item_doc[c.item].add(c.doc)
    out = []
    for (doc, base), cs in groups.items():
        if len(cs) < min_cells:
            continue
        for f in fmts:
            hits = 0
            for c in cs:
                refs = [r for r in _refs(c.text, f) if r.replace(" ", "") != c.item.replace(" ", "")]
                if refs and (f["ref_only"] or any(item_doc.get(_norm_ref(r, f)) for r in refs)):
                    hits += 1
            ratio = hits / len(cs)
            row_fmt = next((g for g in fmts if g is not f and not g["ref_only"]
                            and all(re.match(rf"^{g['core']}$", c.item) for c in cs)), None)
            if ratio >= threshold and row_fmt is not None:
                out.append({"doc": doc, "cell": base, "ratio": round(ratio, 3), "target": f["name"],
                            "row": row_fmt["name"], "n": len(cs)})
    return out


def _norm_ref(ref, f):
    m = re.match(rf"({f['alt']})\s*(?:{f['sep_re']})\s*(\d+)", ref)
    if not m:
        return ref
    p, n = m.groups()
    return f"{p}{f['sep']}{int(n) if f['num_re'] == chr(92) + 'd+' else n}"


def derive_pack(cells, base, config):
    """팩에서 연결 설정(항목 번호 형식, 연결 칸, 문서 역할)을 지우고 문서에서 추론한 값으로 바꾼 팩과 보고."""
    base = pack_mod.load(base)
    fmts = infer_id_formats(cells)
    cols = detect_link_columns(cells, fmts, config.link_threshold)
    by = {f["name"]: f for f in fmts}
    raw = dict(base.raw)
    report = {"id_formats": [{k: f[k] for k in ("name", "prefixes", "sep", "docs", "example")} for f in fmts],
              "link_columns": cols}
    if cols:
        target = Counter(c["target"] for c in cols).most_common(1)[0][0]
        row = Counter(c["row"] for c in cols if c["target"] == target).most_common(1)[0][0]
        req, sec = by[target], by[row]
        num = req["num_re"]
        raw["req"] = {"pattern": rf"^(?:{req['alt']}){req['sep_re']}({num})$",
                      "ref": rf"(?:{req['alt']}){req['sep_re']}({num})", "token": req["core"],
                      "format": f"{req['prefixes'][0]}{req['sep']}{{n}}", "noun": base.req_noun}
        raw["sec"] = {"pattern": rf"^({sec['core']})\b", "full": rf"^{sec['core']}$"}
        raw["link_cells"] = [[c["doc"], c["cell"]] for c in cols if c["target"] == target]
        req_full, sec_full = re.compile(raw["req"]["pattern"]), re.compile(raw["sec"]["full"])
        docs_items = defaultdict(Counter)
        for c in cells:
            if req_full.match(c.item):
                docs_items["req"][c.doc] += 1
            elif sec_full.match(c.item):
                docs_items["sec"][c.doc] += 1
        raw["req_docs"] = [d for d in base.docs if docs_items["req"].get(d)]
        raw["req_doc"] = docs_items["req"].most_common(1)[0][0] if docs_items["req"] else base.req_doc
        raw["sec_docs"] = [d for d in base.docs if docs_items["sec"].get(d)]
        report["roles"] = {"req": req["name"], "sec": sec["name"], "req_docs": raw["req_docs"],
                           "sec_docs": raw["sec_docs"], "req_doc": raw["req_doc"]}
    else:
        raw["link_cells"] = []
        raw["sec"] = {"pattern": r"^(?!x)x", "full": r"^(?!x)x$"}
        report["roles"] = None
    raw["link_na_text"] = ""
    if config.graph_mode == "generic":
        # 도메인층 없음: 규칙 표·판단 칸·코퍼스를 쓰지 않는다
        raw.update({"rules_table": "", "decision_cells": [], "status_cells": [], "status_doc": "", "corpus": None})
    return pack_mod.DomainPack(raw, base.root), report


def _cos(a, b):
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def similarity_edges(g, seeds, embed_fn, threshold=0.7, batch=256):
    """출발 칸 → 다른 문서 칸 유사도 엣지. 이미 다른 엣지(같은 항목, 같은 번호, 추적성, 같은 값)가 있는 쌍은 뺀다."""
    if not seeds or embed_fn is None:
        return []
    cache = getattr(g, "_emb_cache", None)
    if cache is None:
        cache = g._emb_cache = {}
    todo = [c for c in list(seeds) + g.cells if id(c) not in cache]
    for i in range(0, len(todo), batch):
        part = todo[i:i + batch]
        for c, v in zip(part, embed_fn([" ".join(c.text.split())[:1000] for c in part])):
            cache[id(c)] = v
    out = []
    for s in seeds:
        near = {id(x) for x in g.neighbors(s)}
        skeys = {m.key for m in g.cell_mentions(s)}
        for c in g.cells:
            if c.doc == s.doc or id(c) in near or skeys & {m.key for m in g.cell_mentions(c)}:
                continue
            sc = _cos(cache[id(s)], cache[id(c)])
            if sc >= threshold:
                out.append((s, c, round(sc, 4)))
    return out
