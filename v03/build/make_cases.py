"""v0.3 P1 케이스 180개를 sheets/case_spec_180.csv 한 행에 한 케이스씩 만든다.

요청 문장, 값, 정답은 코드가 정한다. T3 추가의 새 Req 문장은 runs/P1/t3_add_drafts.json 을 읽는다.
정답 규칙은 spec.md 9절과 변경 기록(2026-11-04)을 따르며 v02/build/make_cases.py 의 규칙을 옮겼다.
출력: v03/cases/<id>/input(docx 사본, site_index.md, request.txt, request_nl.txt), gold(impact.json, must_not_touch.json),
meta.json, v03/cases/index.json
"""

import csv
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from common import APP_REQS, DOCS, PRODUCT, PROFILE, RUNS, SEED, SERVER_REQS, SHEETS, V03
from datagen import SEC_ITEMS, read_json, with_unit, write_json
from sites import cell_names, site, site_index_md

CASES = V03 / "cases"
SHEET = SHEETS / "case_spec_180.csv"
DRAFTS = RUNS / "t3_add_drafts.json"
DOC_ORDER = ("MDSR", "MDDR", "XXCS")
# T2 정답은 규칙 때문에 판단이 바뀌는 칸만. 같은 행의 나머지 칸과 MDSR 6절 연결 칸은 채점에서 뺀다(neutral).
RULE_CELLS = ("체크리스트 적용 여부", "체크리스트 입증 방법", "적용 여부", "판정")
NEUTRAL_CELLS = ("비고", "해당 요구사항", "시험방법", "확인결과")
LINK_CELLS = {"MDSR": "소프트웨어 기능 요구사항 번호", "XXCS": "해당 요구사항"}
ATTR_KO = PROFILE["cyber_ko"]  # 속성: [이름, false로 바꿀 때 요청 문구, true로 바꿀 때 요청 문구]
COMP_KO = {"앱": "모바일 애플리케이션", "서버": "서버"}
MDDR_DESIGN_PARAS = 3  # 기본 문서의 모든 Req가 설계 3문단이다
NO_LINK = "해당 없음"

FACT_RE = re.compile(r"(F_[A-Z0-9_]+).*?출발 (Req\. \d+)")
ATTR_RE = re.compile(r"\b(" + "|".join(PROFILE["cyber"]) + r")\b")
NEWREQ_RE = re.compile(r"새 (앱|서버) (Req\. \d+)")
TITLE_RE = re.compile(r"새 Req (.+?) 추가")
DEL_RE = re.compile(r"^(Req\. \d+) \(")
DOCS_RE = re.compile(r"\((.+)\)")
REQ_NUM_RE = re.compile(r"Req\. (\d+)")
DIGIT_JONG = {"0": True, "1": True, "2": False, "3": True, "4": False, "5": False, "6": True, "7": True, "8": True,
              "9": False}  # 숫자를 한국어로 읽었을 때 받침 유무(영·일·삼·육·칠·팔)
DIGIT_RIEUL = {"1", "7", "8"}  # 받침이 ㄹ(일·칠·팔)이면 '로'


def _jong(word):
    """마지막 글자의 받침: (받침 있음, ㄹ 받침). 한글·숫자가 아니면 None."""
    ch = word.strip()[-1:]
    if "가" <= ch <= "힣":
        j = (ord(ch) - 0xAC00) % 28
        return j != 0, j == 8
    if ch in DIGIT_JONG:
        return DIGIT_JONG[ch], ch in DIGIT_RIEUL
    return None


def josa(word, pair):
    j = _jong(word)
    if j is None:
        return f"{pair[0]}({pair[1]})"
    return pair[0] if j[0] else pair[1]


def eul(word):
    return word + josa(word, ("을", "를"))


def ro(word):
    j = _jong(word)
    if j is None:
        return word + "(으)로"
    has, rieul = j
    return word + ("으로" if has and not rieul else "로")


def req_num(r):
    return int(REQ_NUM_RE.search(r).group(1))


def add_req(text, rid):
    reqs = [] if text.strip() in (NO_LINK, "-", "") else [x.strip() for x in text.split(",")]
    return ", ".join(sorted(set(reqs) | {rid}, key=req_num))


def remove_req(text, rid):
    reqs = [x.strip() for x in text.split(",") if x.strip() and x.strip() != rid]
    return ", ".join(reqs) if reqs else NO_LINK


class World:
    def __init__(self):
        fj = read_json(PRODUCT / "facts.json")
        lj = read_json(PRODUCT / "links.json")
        self.facts, self.reqs, self.cyber = fj["facts"], fj["reqs"], fj["cyber"]
        self.links, self.rules = lj["links"], lj["rules"]
        self.app = read_json(PRODUCT / "content.json")["applicability"]
        self.sites = read_json(DOCS / "sites.json")["sites"]
        self.by_site = {s["site"]: s for s in self.sites}
        self.fact_sites = defaultdict(list)
        for s in self.sites:
            for f in s["facts"]:
                self.fact_sites[f["fact"]].append((s, f["surface"]))
        self.rel = {(link["req"], link["sec"]): link["relevant"] for link in self.links}
        self.rules_by_attr = defaultdict(list)
        for r in self.rules:
            self.rules_by_attr[r["if"]["attr"]].append(r)

    def item_sites(self, doc, item, cell_prefix=None):
        return [s for s in self.sites if s["doc"] == doc and s["item"] == item
                and (cell_prefix is None or s["cell"].split("#")[0] == cell_prefix)]

    def dependents(self, fid):
        return [g for g, f in self.facts.items() if fid in f["depends_on"]]

    def path_of(self, s, origin_req):
        if s["doc"] == "MDSR" and s["item"] == origin_req:
            return "origin"
        if s["doc"] != "MDSR" and s["item"] == origin_req:
            return "same_id"
        if s["doc"] == "XXCS" and self.rel.get((origin_req, s["item"])) is True:
            return "trace"
        return "same_value"

    def link_cells(self, sec, docs):
        return [s for d in ("MDSR", "XXCS") if d in docs for s in self.item_sites(d, sec, LINK_CELLS[d])]

    def value_of(self, fid, surface):
        fact = self.facts[fid]
        for v in [fact["value"]] + fact["change_candidates"]:
            if with_unit(fact, v) == surface:
                return v
        raise ValueError(f"{fid}: '{surface}' is neither the current value nor a change candidate")


def gold_entry(s, path, **extra):
    return {"site": s["site"], "doc": s["doc"], "item": s["item"], "cell": s["cell"], "path": path, **extra}


def merge(*lists, exclude=()):
    """위치가 겹치면 앞의 것을 남긴다."""
    seen, out = set(exclude), []
    for lst in lists:
        for e in lst:
            if e["site"] not in seen:
                seen.add(e["site"])
                out.append(e)
    return out


# ---- T1 값 변경 ----

def t1_parts(w, fid, rid, docs, nv):
    fact = w.facts[fid]
    new_s = with_unit(fact, nv)
    impact, seen = [], set()
    for f in [fid] + w.dependents(fid):
        for s, surface in w.fact_sites[f]:
            if s["doc"] not in docs or s["site"] in seen:
                continue
            seen.add(s["site"])
            extra = {"op": "modify", "fact": f, "old_surface": surface}
            if f == fid:
                extra["new_value"] = new_s
            else:
                extra.update(new_value=None, derived_from=fid, note="depends_on: 값 재계산 필요")
            impact.append(gold_entry(s, w.path_of(s, rid), **extra))
    traps, trap_seen = [], {e["site"] for e in impact}
    group = fact["same_value_group"]
    if group:
        for g, f in w.facts.items():
            if g != fid and f["same_value_group"] == group:
                for s, surface in w.fact_sites[g]:
                    if s["doc"] in docs and s["site"] not in trap_seen:
                        trap_seen.add(s["site"])
                        traps.append(gold_entry(s, "same_value", fact=g, reason=f"같은 값({surface})이지만 다른 사실"))
    if "XXCS" in docs:
        for (r, sec), relevant in w.rel.items():
            if r == rid and not relevant and w.app[sec]["status"] == "적용":
                for s in w.item_sites("XXCS", sec, "확인결과"):
                    if s["site"] not in trap_seen:
                        trap_seen.add(s["site"])
                        traps.append(gold_entry(s, "trace", reason=f"{rid}와 연결됐지만 relevant=false"))
    title = w.reqs[rid]["title"]
    old_s = with_unit(fact, fact["value"])
    req = {
        "structured": f"{rid} '{title}'의 {eul(fact['name'])} {old_s}에서 {ro(new_s)} 변경한다.",
        "nl": f"{title} 기능에서 {eul(fact['name'])} {ro(new_s)} 바꿔 주세요.",
    }
    change = {"fact": fid, "origin_req": rid, "old_value": fact["value"], "new_value": nv}
    return impact, traps, req, change


# ---- T2 규칙 재계산 ----

def rule_new_value(cell, after):
    if after == "N/A":
        return "N/A" if "적용 여부" in cell else "해당 없음"
    return "적용" if "적용 여부" in cell else None  # 판정·입증 방법은 시험을 해야 정해진다


def t2_parts(w, attrs, docs):
    impact, neutral, changes = [], [], []
    governed = {sec for a in attrs for r in w.rules_by_attr[a] for sec in r["then"]["secs"]}
    for a in attrs:
        for r in w.rules_by_attr[a]:
            for sec in r["then"]["secs"]:
                before = w.app[sec]["status"]
                after = "적용" if before == "N/A" else "N/A"
                source = f"{r['id']}: {r['source']}"
                if "XXCS" in docs:
                    for cell in RULE_CELLS:
                        for s in w.item_sites("XXCS", sec, cell):
                            impact.append(gold_entry(s, "rule", op="modify", rule_source=source,
                                                     applicability_before=before, applicability_after=after,
                                                     new_value=rule_new_value(cell, after)))
                    for cell in NEUTRAL_CELLS:
                        neutral += [gold_entry(s, "rule", reason="같은 행의 부수 칸(채점 제외)")
                                    for s in w.item_sites("XXCS", sec, cell)]
                if "MDSR" in docs:
                    neutral += [gold_entry(s, "rule", reason="6절 연결 칸(채점 제외)")
                                for s in w.item_sites("MDSR", sec, LINK_CELLS["MDSR"])]
        old, new = w.cyber[a], not w.cyber[a]
        changes.append({"attr": a, "old": old, "new": new, "direction": f"{str(old).lower()}->{str(new).lower()}",
                        "rules": [r["id"] for r in w.rules_by_attr[a]]})
    others = [sec for r in w.rules if r["if"]["attr"] not in attrs for sec in r["then"]["secs"] if sec not in governed]
    traps = []
    for sec in others[:3]:
        for s in w.item_sites("XXCS", sec, "적용 여부") + w.item_sites("XXCS", sec, "체크리스트 적용 여부"):
            if s["doc"] in docs:
                traps.append(gold_entry(s, "rule", reason=f"다른 속성 규칙이 정하는 항목({sec}), 이번 변경과 무관"))
    clauses = [f"'{ATTR_KO[c['attr']][0]}'({c['attr']})을 {str(c['old']).lower()}에서 {str(c['new']).lower()}로"
               for c in changes]
    phrases = [ATTR_KO[c["attr"]][2 if c["new"] else 1] for c in changes]
    req = {
        "structured": f"제품 사이버보안 속성 {', '.join(clauses)} 변경한다. 첨부3 적용 예외에 따라 문서를 갱신한다.",
        "nl": "수면코치를 " + " 바꾸고, ".join(phrases) + " 바꾸려고 합니다. 보안 문서에서 달라져야 할 부분을 고쳐 주세요.",
    }
    change = changes[0] if len(changes) == 1 else {"attrs": changes}
    return impact, neutral, traps, req, change


# ---- T3 요구사항 추가·삭제 ----

def insert_entries(doc, rid, cell, texts, path, anchor):
    """새 Req 블록의 칸. 기본 문서에 아직 없는 위치라 op=insert와 기존 위치 anchor를 함께 적는다."""
    return [{"site": site(doc, rid, name), "doc": doc, "item": rid, "cell": name, "path": path,
             "op": "insert", "anchor": anchor, "new_value": t} for name, t in zip(cell_names(cell, len(texts)), texts)]


def t3_add_parts(w, comp, rid, title, draft, docs):
    group = APP_REQS if comp == "앱" else SERVER_REQS
    last = group[-1]
    expected = f"Req. {req_num(last) + 1}"
    if rid != expected:
        raise ValueError(f"new {comp} Req must be {expected} (cases are applied to the base docs), got {rid}")
    if title != draft["title"] or comp != draft["component"]:
        raise ValueError(f"draft mismatch for {rid} '{title}': {draft['component']} '{draft['title']}'")
    if len(draft["기준"]) != 2 or len(draft["설계"]) != MDDR_DESIGN_PARAS:
        raise ValueError(f"draft '{title}' needs 기준 2 and 설계 {MDDR_DESIGN_PARAS} paragraphs")
    mdsr_anchor = w.item_sites("MDSR", last)[-1]["site"]
    impact = []
    for cell, val in (("번호", rid), ("제목", title), ("설명", draft["설명"]), ("목적", draft["목적"])):
        impact += insert_entries("MDSR", rid, cell, [val], "origin", mdsr_anchor)
    impact += insert_entries("MDSR", rid, "기준", draft["기준"], "origin", mdsr_anchor)
    if "MDDR" in docs:
        mddr_anchor = w.item_sites("MDDR", last)[-1]["site"]
        impact += insert_entries("MDDR", rid, "번호", [rid], "same_id", mddr_anchor)
        impact += insert_entries("MDDR", rid, "설계", draft["설계"], "same_id", mddr_anchor)
    secs = draft["links"]
    for sec in secs:
        if w.app[sec]["status"] != "적용":
            raise ValueError(f"link target {sec} for '{title}' is not 적용 in the base documents")
        for s in w.link_cells(sec, docs):
            impact.append(gold_entry(s, "trace", op="modify", new_value=add_req(s["text"], rid),
                                     note=f"연결 Req 칸에 {rid} 추가"))
    cat = secs[0].split("-")[0]
    others = [s for s in SEC_ITEMS if s.startswith(cat + "-") and s not in secs and w.app[s]["status"] == "적용"]
    traps = []
    for sec in others[:2]:
        for s in w.link_cells(sec, docs):
            traps.append(gold_entry(s, "trace", reason=f"같은 분류({cat})지만 새 기능과 무관"))
    req = {
        "structured": f"{COMP_KO[comp]} 요구사항 {rid} '{title}'을(를) 신규 추가한다. 내용: {draft['설명']}",
        "nl": f"{comp}에 새 기능을 넣어 주세요. {draft['기능']}{josa(draft['기능'], ('이', '가'))} 필요합니다.",
    }
    change = {"new_req": rid, "component": comp, "title": title, "related_secs": secs,
              "draft": {k: draft[k] for k in ("설명", "목적", "기준", "설계")}}
    return impact, traps, req, change


def t3_delete_parts(w, rid, docs):
    title = w.reqs[rid]["title"]
    if w.reqs[rid]["security"]:
        raise ValueError(f"{rid} is a security requirement and must not be deleted (spec 7)")
    impact = []
    for s in w.item_sites("MDSR", rid):
        if s["cell"] != "번호":
            impact.append(gold_entry(s, "origin", op="modify", new_value="삭제"))
    if "MDDR" in docs:
        for s in w.item_sites("MDDR", rid, "설계"):
            impact.append(gold_entry(s, "same_id", op="modify", new_value="삭제"))
    for sec in sorted({link["sec"] for link in w.links if link["req"] == rid}):
        if w.app[sec]["status"] != "적용":
            continue
        for s in w.link_cells(sec, docs):
            impact.append(gold_entry(s, "trace", op="modify", new_value=remove_req(s["text"], rid),
                                     note=f"연결 Req 칸에서 {rid} 제거"))
    group = APP_REQS if rid in APP_REQS else SERVER_REQS
    following = group[group.index(rid) + 1: group.index(rid) + 3]
    traps = []
    for r2 in following:
        for d in ("MDSR", "MDDR"):
            if d in docs:
                traps += [gold_entry(s, "same_id", reason="삭제 후 번호 재정렬 금지") for s in w.item_sites(d, r2, "번호")]
    req = {
        "structured": f"{rid} '{title}' 요구사항을 삭제한다. 요구사항 번호는 유지하고 '삭제'로 표기한다.",
        "nl": f"{title} 기능을 제품에서 빼 주세요. 다른 요구사항 번호는 그대로 두세요.",
    }
    return impact, traps, req, {"deleted_req": rid, "title": title}


# ---- 시트 한 행 → 케이스 ----

def read_sheet(path=SHEET):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if r and not r[0].startswith("#")]
    header = rows[0]
    return [dict(zip(header, r)) for r in rows[1:]]


def docs_of(cell):
    docs = set(DOCS_RE.search(cell).group(1).split("+"))
    if not docs <= set(DOC_ORDER):
        raise ValueError(f"bad document set: {cell}")
    return docs


def value_change(text):
    """'15분→20분' 또는 '…, 15분→20분' → (옛 값, 새 값)."""
    old, new = text.rsplit(", ", 1)[-1].split("→")
    return old.strip(), new.strip()


def t1_from_row(w, what, change_text, docs):
    m = FACT_RE.search(what)
    fid, rid = m.groups()
    old_s, new_s = value_change(change_text)
    if w.value_of(fid, old_s) != w.facts[fid]["value"]:
        raise ValueError(f"{fid}: old value '{old_s}' is not the current value")
    nv = w.value_of(fid, new_s)
    if fid not in w.reqs[rid]["uses"]:
        raise ValueError(f"{rid} does not use {fid}")
    return t1_parts(w, fid, rid, docs, nv)


def build_case(w, row, drafts):
    kind, what, change_text = row["유형"], row["무엇을 바꾸나"], row["변경"]
    docs = docs_of(row["엮인 문서 수"])
    neutral = []
    if kind == "T1":
        impact, traps, req, change = t1_from_row(w, what, change_text, docs)
        case_type, subtype = "T1", "value_change"
    elif kind == "T2":
        attrs = ATTR_RE.findall(what)
        impact, neutral, traps, req, change = t2_parts(w, attrs, docs)
        case_type, subtype = "T2", "rule_flip"
    elif kind == "T3-삭제":
        impact, traps, req, change = t3_delete_parts(w, DEL_RE.match(what).group(1), docs)
        case_type, subtype = "T3", "delete"
    elif kind in ("T3-추가", "T3-추가복합"):
        comp, rid = NEWREQ_RE.search(what).groups()
        title = TITLE_RE.search(change_text).group(1)
        impact, traps, req, change = t3_add_parts(w, comp, rid, title, drafts[row["case_id"]], docs)
        case_type, subtype = "T3", "add"
        if kind == "T3-추가복합":
            if FACT_RE.search(what):
                i2, tr2, r2, c2 = t1_from_row(w, what, change_text, docs)
                change["value_change"], subtype = c2, "add_with_value_change"
            else:
                i2, neutral, tr2, r2, c2 = t2_parts(w, ATTR_RE.findall(what.split("+", 1)[1]), docs)
                change["attr_change"], subtype = c2, "add_with_attr_change"
            impact = merge(impact, i2)
            traps = merge(traps, tr2, exclude={e["site"] for e in impact})
            req = {"structured": f"{req['structured']} 또한 {r2['structured']}", "nl": f"{req['nl']} 그리고 {r2['nl']}"}
    else:
        raise ValueError(f"unknown type {kind}")
    taken = {e["site"] for e in impact} | {e["site"] for e in traps}
    neutral = merge([e for e in neutral if e["site"] not in taken])
    return {"case_type": case_type, "subtype": subtype, "docs": [d for d in DOC_ORDER if d in docs],
            "impact": impact, "traps": traps, "neutral": neutral, "request_structured": req["structured"],
            "request_nl": req["nl"], "change": change, "sheet": row}


def write_case(cid, c, w):
    root = CASES / cid
    if root.exists():
        shutil.rmtree(root)
    (root / "input").mkdir(parents=True)
    (root / "gold").mkdir()
    for d in c["docs"]:
        shutil.copy(DOCS / f"{d}.docx", root / "input" / f"{d}.docx")
    subset = [s for s in w.sites if s["doc"] in c["docs"]]
    (root / "input" / "site_index.md").write_text(site_index_md(subset), encoding="utf-8")
    (root / "input" / "request.txt").write_text(c["request_structured"] + "\n", encoding="utf-8")
    (root / "input" / "request_nl.txt").write_text(c["request_nl"] + "\n", encoding="utf-8")
    write_json(root / "gold" / "impact.json", {"case_id": cid, "case_type": c["case_type"], "subtype": c["subtype"],
                                                 "change": c["change"], "items": c["impact"], "neutral": c["neutral"]})
    write_json(root / "gold" / "must_not_touch.json", {"case_id": cid, "items": c["traps"]})
    meta = {"case_id": cid, "case_type": c["case_type"], "subtype": c["subtype"], "n_docs": len(c["docs"]),
            "docs": c["docs"], "paths": dict(Counter(e["path"] for e in c["impact"])),
            "trap_paths": dict(Counter(e["path"] for e in c["traps"])),
            "n_impact": len(c["impact"]), "n_traps": len(c["traps"]), "n_neutral": len(c["neutral"]),
            "request_structured": c["request_structured"], "request_nl": c["request_nl"],
            "sheet_type": c["sheet"]["유형"], "sheet_paths": c["sheet"]["노리는 경로"], "seed": SEED}
    write_json(root / "meta.json", meta)
    return meta


def main():
    w = World()
    rows = read_sheet()
    drafts = {d["case_id"]: d for d in read_json(DRAFTS)["drafts"]}
    cases = [(row["case_id"], build_case(w, row, drafts)) for row in rows]
    if len(cases) != 180 or len({cid for cid, _ in cases}) != 180:
        raise SystemExit(f"expected 180 distinct cases, got {len(cases)}")
    if CASES.exists():
        shutil.rmtree(CASES)
    metas = [write_case(cid, c, w) for cid, c in cases]
    write_json(CASES / "index.json", metas)
    paths = Counter(p for m in metas for p, n in m["paths"].items() for _ in range(n))
    print(f"wrote {len(metas)} cases; gold cells by path: {dict(paths)}")
    for t in ("T1", "T2", "T3"):
        print(t, "n_docs:", dict(sorted(Counter(m["n_docs"] for m in metas if m["case_type"] == t).items())))


if __name__ == "__main__":
    main()
