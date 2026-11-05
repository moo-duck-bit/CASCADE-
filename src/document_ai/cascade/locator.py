"""Locator: 그래프로 후보 칸을 모으고, LLM 1회 호출로 후보마다 관련성을 판단한다.

후보 종류
문서 이름·칸 이름은 도메인 팩에서 읽는다(아래 설명은 EC-SW 팩 기준).
- T1: origin(출발 Req의 MDSR 칸) · same_id(다른 문서의 같은 Req 칸) · trace(출발 Req와 연결된 보안 항목 행) · value(그 밖에 같은 값)
       모두 옛 값을 담은 칸만 모은다(값 색인. 끄면 표기 글자가 같은 칸만).
- T2: rule(규칙 표로 적용 여부가 뒤집히는 항목의 판단 칸 4종, 판단 없이 관련) · rule_link(그 항목의 연결 칸)
- T3 추가: insert(새 Req 칸, 앵커 뒤) · link(관련 보안 항목의 MDSR 6절 Req 번호 칸과 XXCS 해당 요구사항 칸)
- T3 삭제: delete(삭제 Req의 칸, 번호 제외) · unlink(그 Req 번호가 적힌 연결 칸) · ref(본문에서 그 Req를 가리키는 칸)
"""

import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from . import rules as rules_mod
from .cells import site_of
from .llm import load_prompt
from .request import attr_changes, current_attrs

PROMPT = "locator_v2"

SAME_FACT = ("이 칸에 바뀌는 사실('{target}')의 값이 나오는가? 다른 요구사항·절·문서의 칸이어도, 칸 안에 다른 사실이 "
             "함께 있어도, 같은 사실이 한 번이라도 나오면 true. 숫자만 같고 다른 사실이면 false.")
Q = {
    "origin": SAME_FACT,
    "same_id": SAME_FACT,
    "value": SAME_FACT,
    "similar": SAME_FACT,
    "trace": ("이 보안 시험 칸이 바뀌는 사실('{target}')을 시험 조건이나 결과로 다루는가? 지금 적힌 값은 옛 값이므로, "
              "그 사실을 다루면 새 값으로 고쳐야 하니 true. 그 사실과 무관한 시험이면(숫자만 같으면) false."),
    "rule_link": "보안 항목의 적용 여부가 바뀌는 데 따라 이 연결 칸도 고쳐야 하는가?",
    "rule_sec": "속성 변경과 위 적용 예외 규칙에 따라 이 보안 항목의 적용 여부가 바뀌는가?",
    "link": ("새 요구사항의 기능이 이 보안 통제를 직접 구현하거나 이 보안 시험의 대상이 되는가? 현재 연결된 요구사항들과 "
             "같은 성격이면 true. 모든 기능에 두루 걸치는 일반 항목은 새 기능이 그 통제를 직접 다루지 않으면 false."),
    "ref": "이 칸이 삭제되는 요구사항을 가리키는가?",
}


@dataclass(eq=False)
class Cand:
    site: str
    kind: str
    cell: object = None        # 기존 칸(insert는 None)
    sec: str = ""              # 보안 항목 단위 판단(T2 규칙 끔, T3 추가)
    op: str = "modify"
    note: str = ""


@dataclass
class LocResult:
    candidates: list
    relevant: list
    judgments: list = field(default_factory=list)
    anchors: dict = field(default_factory=dict)
    rule_info: dict = field(default_factory=dict)
    attrs: dict = field(default_factory=dict)
    verifier_scope: set = field(default_factory=set)
    evidence: list = field(default_factory=list)       # 규정 검색 결과(T2·T3 추가)  # V2b·V6b가 지적해 Editor에 넘긴 칸(V5 대상에서 뺀다)


def _add(out, seen, cand):
    if cand.site not in seen:
        seen.add(cand.site)
        out.append(cand)


# T1

def t1_candidates(ch, g, config):
    origin, out, seen, p = ch.origin_req, [], set(), g.pack
    odoc = ch.origin_doc or p.req_doc
    secs = g.secs_of_req(origin)
    hits = g.value_cells(ch.old, normalized=config.value_index, converted=config.value_index)
    if not any(c.item == origin and c.doc == odoc for c in hits):
        # 출발 Req에서 옛 값을 못 찾으면(표기 차이) 출발 Req 칸 전체를 후보로 둔다
        hits = [c for c in g.item_cells(odoc, origin) if c.base not in p.header_cells] + hits
    for c in hits:
        if c.item == origin:
            kind = "origin" if c.doc == odoc else "same_id"
        elif c.doc in p.sec_docs and c.doc not in p.req_docs and c.item in secs:
            kind = "trace"
        else:
            kind = "value"
        _add(out, seen, Cand(c.site, kind, c))
    embed_fn = getattr(g, "_embed_fn", None)
    if config.graph_mode != "pack" and embed_fn is not None:
        # 범용층 유사도 엣지: 출발 Req 칸과 비슷한데 다른 엣지가 없는 다른 문서 칸
        from .generic_graph import similarity_edges
        seeds = [c for c in g.item_cells(odoc, origin) if c.base not in p.header_cells]
        for _, c, _ in similarity_edges(g, seeds, embed_fn, config.sim_threshold):
            _add(out, seen, Cand(c.site, "similar", c))
    return out


# T2

def rules_text(table):
    lines = ["## 첨부3 적용 예외 규칙"]
    for r in table:
        cond = "true" if r["equals"] else "false"
        other = rules_mod.FLIP[r["result"]]
        lines.append(f"- {r['attr']}({r['attr_ko']})가 {cond}이면 {', '.join(r['secs'])}은 {r['result']}, "
                     f"아니면 {other} ({r['source']})")
    return "\n".join(lines) + "\n"


def t2_candidates(ch, g, config, res):
    out, seen = [], set()
    attrs, notes = current_attrs(g, ch)
    res.attrs = attrs
    pairs = [(rules_mod.resolve_attr(a, ko, g.rule_table), old, new) for a, ko, old, new in attr_changes(ch)]
    attr = pairs[0][0]
    if config.rules and all(k is not None for k, _, _ in pairs):
        flips = rules_mod.flips_many(pairs, attrs, g.rule_table)
        res.rule_info = {"flips": flips, "notes": notes, "attr": attr}
        if len(pairs) > 1:
            res.rule_info["attrs"] = [k for k, _, _ in pairs]
        for f in flips:
            for c in g.item_cells(g.pack.status_doc, f["sec"]):
                if c.base in g.pack.decision_cells:
                    _add(out, seen, Cand(c.site, "rule", c, sec=f["sec"], note=f"{f['before']}→{f['after']}"))
            for c in g.trace_cells(f["sec"]):
                _add(out, seen, Cand(c.site, "rule_link", c, sec=f["sec"]))
    else:
        # 규칙을 껐거나 규칙 표에 없는 속성: 보안 항목마다 LLM이 판단(검색 근거가 있으면 함께 준다)
        res.rule_info = {"flips": None, "notes": notes, "attr": attr,
                         "fallback": "rules_off" if not config.rules else "no_rule_for_attr"}
        if len(pairs) > 1:
            res.rule_info["attrs"] = [k for k, _, _ in pairs]
        dc = g.pack.decision_cells
        for sec in g.sec_items():
            if dc:
                cells = [c for c in g.item_cells(g.pack.status_doc, sec) if c.base in dc]
            else:  # 판단 칸을 모르는 범용층: 연결 항목의 연결 칸 밖 모든 칸
                cells = [c for d in g.pack.sec_docs for c in g.item_cells(d, sec) if not g.pack.is_link(d, c.base)]
            for c in cells:
                _add(out, seen, Cand(c.site, "rule", c, sec=sec))
    return out


# T3

def anchor_req(g, doc, new_req):
    p = g.pack
    n = p.req_num(new_req)
    reqs = [r for r in g.req_items(doc) if p.same_component(p.req_num(r), n) and p.req_num(r) < n]
    return max(reqs, key=p.req_num) if reqs else None


def template_cells(g, doc, anchor, new_req):
    """새 Req 칸 이름: 앵커 항목의 칸 이름 순서. 여러 문단 칸의 문단 수는 같은 문서·같은 구성요소의 기존 Req 블록에서
    그 칸 문단 수의 중앙값(낮은 쪽)으로 정한다. 개발셋에서 고른 고정값은 쓰지 않는다."""
    p = g.pack
    n = p.req_num(new_req)
    labels = []
    for c in g.item_cells(doc, anchor):
        if c.base not in labels:
            labels.append(c.base)
    peers = [r for r in g.req_items(doc) if p.same_component(p.req_num(r), n)] or g.req_items(doc)
    out = []
    for label in labels:
        per_req = [k for k in (sum(1 for c in g.item_cells(doc, r) if c.base == label) for r in peers) if k]
        k = max(1, statistics.median_low(per_req)) if per_req else 1
        out += [label] if k == 1 else [f"{label}#p{i}" for i in range(1, k + 1)]
    return out


def _req_title(g, req):
    for c in g.item_cells(g.pack.req_doc, req):
        if c.base == g.pack.title_cell:
            return c.text.strip()
    return ""


def sec_title(g, sec):
    for doc, name in g.pack.sec_title_cells:
        for c in g.item_cells(doc, sec):
            if c.base == name:
                return c.text.strip()
    return sec


def t3_add_candidates(ch, g, res, config):
    out, seen = [], set()
    for doc in g.pack.req_docs:
        if not g.docs.get(doc):
            continue
        anchor = anchor_req(g, doc, ch.new_req)
        if anchor is None:
            continue
        res.anchors[doc] = anchor
        for name in template_cells(g, doc, anchor, ch.new_req):
            _add(out, seen, Cand(site_of(doc, ch.new_req, name), "insert", None, op="insert", note=anchor))
    for sec in g.sec_items():
        if g.status(sec) != "적용":
            continue
        for c in g.trace_cells(sec):
            _add(out, seen, Cand(c.site, "link", c, sec=sec))
    return out


def t3_delete_candidates(ch, g):
    out, seen, p = [], set(), g.pack
    for doc in p.req_docs:
        for c in g.item_cells(doc, ch.req):
            if c.base != p.number_cell:
                _add(out, seen, Cand(c.site, "delete", c, op="mark_deleted"))
    for c in g.cells:
        if c.item == ch.req or ch.req not in p.req_refs(c.text):
            continue
        kind = "unlink" if p.is_link(c.doc, c.base) else "ref"
        _add(out, seen, Cand(c.site, kind, c))
    return out


# ledger_like

def _ngrams(text, n=3):
    t = re.sub(r"\s+", " ", text.lower())
    return Counter(t[i:i + n] for i in range(len(t) - n + 1))


def cosine(a, b):
    num = sum(v * b.get(k, 0) for k, v in a.items())
    den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return num / den if den else 0.0


def ledger_candidates(ch, g, config, res):
    """그래프 1-hop 확장 + 문자 3-gram 코사인 유사 칸. 값 색인·규칙 표·관련성 판단을 쓰지 않는다."""
    if ch.kind == "T1":
        seeds = [c for c in g.item_cells(g.pack.req_doc, ch.origin_req) if c.base not in g.pack.header_cells]
    elif ch.kind == "T3_delete":
        seeds = [c for c in g.item_cells(g.pack.req_doc, ch.req)]
    else:
        q = _ngrams(ch.text)
        scored = sorted(g.cells, key=lambda c: -cosine(q, _ngrams(c.text)))
        seeds = scored[:5]
    out, seen = [], set()
    if ch.kind == "T3_add":
        out = [c for c in t3_add_candidates(ch, g, res, config) if c.kind == "insert"]
        seen = {c.site for c in out}
    for s in seeds:
        _add(out, seen, Cand(s.site, "seed", s))
    for s in seeds:
        for c in g.neighbors(s):
            _add(out, seen, Cand(c.site, "hop", c))
    grams = [_ngrams(s.text) for s in seeds]
    for c in g.cells:
        if c.site not in seen and any(cosine(gm, _ngrams(c.text)) >= config.ngram_threshold for gm in grams):
            _add(out, seen, Cand(c.site, "similar", c))
    return out


# 관련성 판단

def change_info(ch, g=None):
    if ch.kind == "T1":
        info = f"출발 요구사항: {ch.origin_req} '{ch.title}'\n바뀌는 사실: {ch.target}\n옛 값: {ch.old}\n새 값: {ch.new}"
        if g is not None:
            desc = [c.text for c in g.item_cells(g.pack.req_doc, ch.origin_req) if c.base == g.pack.desc_cell]
            if desc:
                info += f"\n출발 요구사항 설명(사실의 뜻을 판단할 때 참고): {' '.join(desc[0].split())}"
        return info
    if ch.kind == "T2":
        parts = attr_changes(ch)
        if len(parts) == 1:
            return f"사이버보안 속성: {ch.attr}({ch.attr_ko})\n옛 값: {str(ch.old).lower()}\n새 값: {str(ch.new).lower()}"
        return "\n".join(f"사이버보안 속성 {i}: {a}({ko})\n옛 값: {str(o).lower()}\n새 값: {str(n).lower()}"
                         for i, (a, ko, o, n) in enumerate(parts, 1))
    if ch.kind == "T3_add":
        return f"새 요구사항: {ch.new_req} '{ch.title}' ({ch.component})\n내용: {ch.content}"
    return f"삭제할 요구사항: {ch.req} '{ch.title}'"


def is_auto(c):
    """구조로 정해져 판단을 묻지 않는 후보. 규칙 표로 뒤집힌 rule 칸은 note(전→후)가 있다."""
    return c.kind in ("insert", "delete", "unlink") or (c.kind == "rule" and bool(c.note))


def judge_units(cands):
    """판단 단위: 칸 하나, 또는 보안 항목 하나(T2 규칙 끔의 rule, T3 추가의 link)."""
    units, by_sec = [], {}
    for c in cands:
        if is_auto(c):
            continue
        if c.kind in ("link", "rule"):
            key = (c.sec, c.kind)
            if key not in by_sec:
                by_sec[key] = {"kind": "rule_sec" if c.kind == "rule" else "link", "sec": c.sec, "cands": []}
                units.append(by_sec[key])
            by_sec[key]["cands"].append(c)
        else:
            units.append({"kind": c.kind, "sec": "", "cands": [c]})
    return units


def unit_line(i, u, g, ch):
    q = {**Q, **g.pack.questions}[u["kind"]].format(target=ch.target or "")
    if u["sec"]:
        status = g.status(u["sec"]) or "?"
        text = f"보안 항목 {u['sec']} '{sec_title(g, u['sec'])}' (현재 적용 여부: {status})"
        ev = sorted({h["clause_id"] for h in getattr(g, "_evidence", []) if h.get("sec_id") == u["sec"]})
        if ev:
            text += f"; 검색 근거: {', '.join(ev)}"
        if u["kind"] == "link":
            reqs = sorted(g.reqs_of_sec(u["sec"]), key=g.pack.req_num)
            titles = [f"{r} '{_req_title(g, r)}'" for r in reqs]
            text += f"; 현재 연결된 요구사항: {', '.join(titles) or '없음'}"
        return f"- id={i} | [보안 항목 | {u['sec']}] | {u['kind']} | {q} | {text}"
    c = u["cands"][0]
    return f"- id={i} | {c.site} | {c.kind} | {q} | {' '.join(c.cell.text.split())}"


def locate(ch, g, config, llm, log, evidence=None):
    res = LocResult([], [])
    res.evidence = list(evidence or [])
    g._evidence = res.evidence
    if config.mode == "ledger_like":
        res.candidates = ledger_candidates(ch, g, config, res)
        res.relevant = list(res.candidates)
        log.stage("locator", candidates=len(res.candidates), relevant=len(res.relevant), mode="ledger_like")
        return res
    if ch.kind == "T1":
        res.candidates = t1_candidates(ch, g, config)
    elif ch.kind == "T2":
        res.candidates = t2_candidates(ch, g, config, res)
    elif ch.kind == "T3_add":
        res.candidates = t3_add_candidates(ch, g, res, config)
    else:
        res.candidates = t3_delete_candidates(ch, g)
    units = judge_units(res.candidates)
    keep = {c.site for c in res.candidates if is_auto(c)}
    if units and config.relevance:
        system, user_tpl = load_prompt(PROMPT, g.pack)
        rule_text = rules_text(g.rule_table) if (ch.kind == "T2" and res.rule_info.get("fallback")) else ""
        if ch.kind == "T2" and res.rule_info.get("fallback") and g.rules_doc.get("notes"):
            rule_text += "".join(f"- 참고({n['attr']}): {n['text']}\n" for n in g.rules_doc["notes"])
        if res.evidence:
            from .retrieval import evidence_text
            rule_text += "\n" + evidence_text(res.evidence)
        lines = [unit_line(i, u, g, ch) for i, u in enumerate(units)]
        user = user_tpl.format(request=ch.text, change_info=change_info(ch, g), rule_text=rule_text,
                               candidates="\n".join(lines))
        schema = {"type": "object", "required": ["judgments"], "properties": {"judgments": {"type": "array", "items": {
            "type": "object", "required": ["id", "relevant", "reason"],
            "properties": {"id": {"type": "integer"}, "relevant": {"type": "boolean"},
                           "reason": {"type": "string"}}}}}}
        out, usage = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                   schema, "locator")
        log.llm_usage("locator", usage)
        log.stage("locator", context_chars=len(system) + len(user))
        verdict = {j["id"]: j for j in out["judgments"]}
        for i, u in enumerate(units):
            j = verdict.get(i)
            rel = bool(j and j["relevant"])
            res.judgments.append({"id": i, "kind": u["kind"], "sec": u["sec"],
                                  "sites": [c.site for c in u["cands"]], "relevant": rel,
                                  "reason": (j or {}).get("reason", "판단 누락")})
            if rel:
                keep |= {c.site for c in u["cands"]}
    else:
        keep |= {c.site for u in units for c in u["cands"]}
    res.relevant = [c for c in res.candidates if c.site in keep]
    if ch.kind == "T2" and res.rule_info.get("fallback"):
        # 규칙을 끄거나 규칙이 없으면 LLM이 고른 항목이 곧 뒤집히는 항목이다
        secs = sorted({c.sec for c in res.relevant})
        res.rule_info["flips"] = [{"sec": s, "before": g.status(s), "after": rules_mod.FLIP.get(g.status(s), "?"),
                                   "rule": None} for s in secs]
    log.stage("locator", candidates=len(res.candidates), units=len(units), relevant=len(res.relevant),
              by_kind=dict(Counter(c.kind for c in res.relevant)))
    return res
