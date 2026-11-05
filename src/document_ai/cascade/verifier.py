"""결정론 Verifier. 규칙은 verifier_spec.md v2(구현 전에 고정)를 따른다.

V2·V5·V6은 Locator 결과를 쓰고, V1·V2b·V3·V3b·V4·V6b는 쓰지 않는다.

적용한 사본을 다시 파싱한 칸만 보고 지적 [{type, site, detail}]을 낸다. 정답 쪽 자료는 보지 않는다.
"""

import random

from . import rules as rules_mod
from . import values
from .locator import anchor_req
from .request import attr_changes

VALUE_KINDS = ("origin", "same_id", "trace", "value")
NA_TEXT = "해당 없음"


def issue(t, site, detail):
    return {"type": t, "site": site, "detail": detail}


def _has_value(text, surface, norm=None):
    norm = norm or values.DEFAULT
    key = norm.key_of(surface)
    if key is None:
        return values.loose_contains(text, surface)
    return any(m.key == key for m in norm.mentions(text))


def old_left(text, old, new, norm=None):
    norm = norm or values.DEFAULT
    if not _has_value(text, old, norm):
        return False
    if norm.key_of(old) is None and values.loose_core(old) in values.loose_core(new):
        return not _has_value(text, new, norm)  # 새 값이 옛 값을 품는 경우: 새 값이 있으면 해결
    return True


def _status(text):
    t = text.strip()
    return "N/A" if t.upper().startswith("N/A") else ("적용" if t.startswith("적용") else t)


def verify(ch, res, edits, graph, applied_cells, config, apply_result=None):
    out, p, norm = [], graph.pack, graph.values
    after = {}
    for c in applied_cells:
        after.setdefault(c.site, c)
    orig_sites = set(graph.by_site)
    edit_sites = {e["site"] for e in edits}

    if config.check_on("V1"):
        failed = {f["site"] for f in (apply_result.failed if apply_result else [])}
        for e in edits:
            if e["op"] != "insert" and e["site"] not in orig_sites:
                out.append(issue("V1", e["site"], "원본 문서에 없는 위치"))
            elif e["site"] in failed:
                out.append(issue("V1", e["site"], "사본에 쓰지 못함"))
            elif e["op"] == "insert" and e["site"] not in after:
                out.append(issue("V1", e["site"], "추가한 칸이 사본에서 이 위치로 읽히지 않음"))

    relevant = {c.site for c in res.relevant}
    cand = {c.site for c in res.candidates}
    if ch.kind == "T1":
        vcells = [c for c in res.relevant if c.kind in VALUE_KINDS and c.site in after]
        for c in vcells:
            text = after[c.site].text
            if config.check_on("V2") and old_left(text, ch.old, ch.new, norm):
                out.append(issue("V2", c.site, f"관련 칸에 옛 값 {ch.old}이(가) 남아 있음"))
            if config.check_on("V6") and not _has_value(text, ch.new, norm):
                out.append(issue("V6", c.site, f"같은 사실로 판정된 칸인데 새 값 {ch.new}이(가) 없음"))

    if ch.kind == "T3_delete" and config.check_on("V3"):
        for c in applied_cells:
            if c.item == ch.req:
                if c.doc in p.req_docs and c.base != p.number_cell and c.text.strip() != "삭제":
                    out.append(issue("V3", c.site, f"삭제한 {ch.req}의 칸이 '삭제'로 표기되지 않음"))
            elif ch.req in p.req_refs(c.text):
                out.append(issue("V3", c.site, f"삭제한 {ch.req}을(를) 아직 가리킴"))

    if ch.kind == "T3_add" and config.check_on("V3"):
        link_cells = [c for c in applied_cells if p.is_link(c.doc, c.base)]
        linked = [c for c in link_cells if ch.new_req in p.req_refs(c.text)]
        if not linked:
            num = f"[{p.req_doc} | {ch.new_req} | {p.number_cell}]"
            out.append(issue("V3", num, f"새 {ch.new_req}이(가) 어느 연결 칸에도 없음"))
        by_sec = {}
        for c in link_cells:
            by_sec.setdefault(c.item, {})[c.doc] = c
        for sec, d in by_sec.items():
            # 같은 연결 항목의 연결 칸이 두 문서 이상에 있을 때 한쪽에만 새 Req가 있으면 빠진 쪽을 지적
            has = {doc: ch.new_req in p.req_refs(c.text) for doc, c in d.items()}
            if len(d) >= 2 and any(has.values()) and not all(has.values()):
                for doc, c in d.items():
                    if not has[doc]:
                        out.append(issue("V3", c.site, f"{sec}의 다른 문서 연결 칸에는 {ch.new_req}이(가) 있는데 여기는 없음"))

    if ch.kind == "T2" and config.rules and config.check_on("V4"):
        attrs = dict(res.attrs)
        for a, ko, _, new in attr_changes(ch):
            attrs[rules_mod.resolve_attr(a, ko, graph.rule_table) or a] = new
        expect = rules_mod.applicability(attrs, graph.rule_table)
        clauses = rules_mod.sec_clauses(graph.rule_table)
        for sec, want in expect.items():
            text_cells = [k for k in p.decision_cells if k not in p.status_cells]
            for c in applied_cells:
                if c.doc != p.status_doc or c.item != sec:
                    continue
                basis = f" (근거: {', '.join(clauses.get(sec, []))})" if clauses.get(sec) else ""
                if c.base in p.status_cells and _status(c.text) != want:
                    out.append(issue("V4", c.site, f"규칙 재계산 결과 {want}인데 '{c.text.strip()}'{basis}"))
                elif c.base in text_cells:
                    is_na = c.text.strip() == NA_TEXT
                    if want == "N/A" and not is_na:
                        out.append(issue("V4", c.site, f"적용 여부가 N/A인 항목인데 '{NA_TEXT}'가 아님{basis}"))
                    elif want == "적용" and is_na:
                        out.append(issue("V4", c.site, f"적용 항목인데 '{NA_TEXT}'로 남아 있음{basis}"))

    if ch.kind == "T1" and (config.check_on("V2b") or config.check_on("V6b")):
        # Locator와 독립: 출발 Req·같은 번호 항목·trace 행은 옛 값 잔존(V2b), 그 밖의 같은 값 미편집 칸은 확인 요청(V6b)
        items = {(d, ch.origin_req) for d in p.docs} | {(d, s) for s in graph.secs_of_req(ch.origin_req) for d in p.sec_docs}
        scope = [c for key in items for c in graph.items.get(key, []) if c.base != p.number_cell]
        scope_sites = {c.site for c in scope}
        if config.check_on("V2b"):
            for c in scope:
                if c.site in after and old_left(after[c.site].text, ch.old, ch.new, norm):
                    out.append(issue("V2b", c.site, f"출발 요구사항·같은 번호·연결 행 범위에 옛 값 {ch.old}이(가) 남아 있음(확인 필요)"))
        if config.check_on("V6b"):
            for c in graph.value_cells(ch.old, normalized=True, converted=False):
                if c.site not in edit_sites and c.site not in scope_sites:
                    out.append(issue("V6b", c.site, f"옛 값 {ch.old}과(와) 같은 값이 있는 칸: 같은 사실인지 확인"))

    if ch.kind in ("T3_add", "T3_delete") and config.check_on("V3b"):
        link_cells = [c for c in applied_cells if p.is_link(c.doc, c.base)]
        if ch.kind == "T3_delete":
            for c in link_cells:
                if ch.req in p.req_refs(c.text):
                    out.append(issue("V3b", c.site, f"삭제한 {ch.req}이(가) 연결 칸에 남아 있음"))
        else:
            for doc in p.req_docs:
                if graph.docs.get(doc) and anchor_req(graph, doc, ch.new_req):
                    num = f"[{doc} | {ch.new_req} | {p.number_cell}]"
                    if num not in after:
                        out.append(issue("V3b", num, f"{doc}에 새 {ch.new_req} 블록이 없음"))
            by_sec = {}
            for c in link_cells:
                by_sec.setdefault(c.item, {})[c.doc] = c
            for sec, d in by_sec.items():
                has = {doc: ch.new_req in p.req_refs(c.text) for doc, c in d.items()}
                if len(d) >= 2 and any(has.values()) and not all(has.values()):
                    for doc, c in d.items():
                        if not has[doc]:
                            out.append(issue("V3b", c.site, f"{sec}의 다른 문서 연결 칸에는 {ch.new_req}이(가) 있는데 여기는 없음"))

    if config.check_on("V5"):
        scope_ok = getattr(res, "verifier_scope", set())
        for e in edits:
            if e["site"] in scope_ok:
                continue
            if e["site"] not in cand and e["op"] != "insert":
                out.append(issue("V5", e["site"], "후보 밖의 칸을 고침"))
            elif e["site"] in cand and e["site"] not in relevant:
                out.append(issue("V5", e["site"], "관련 없음으로 판정된 칸을 고침"))

    # Locator 독립 검사가 v1 검사와 같은 칸을 지적하면 v1 검사만 남긴다
    base_of = {"V2b": "V2", "V3b": "V3"}
    have = {(i["type"], i["site"]) for i in out}
    out = [i for i in out if not (i["type"] in base_of and (base_of[i["type"]], i["site"]) in have)]
    seen, uniq = set(), []
    for i in out:
        k = (i["type"], i["site"])
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    return uniq


def fake_issues(real, cells, seed, case_id, round_no):
    """대조군: 실제 지적과 같은 유형·개수, 위치는 무작위."""
    rng = random.Random(f"{seed}|{case_id}|{round_no}")
    sites = sorted({c.site for c in cells})
    out = []
    for i in real:
        out.append(issue(i["type"], rng.choice(sites), "검사에서 문제가 있을 수 있는 칸으로 표시됨"))
    return out
