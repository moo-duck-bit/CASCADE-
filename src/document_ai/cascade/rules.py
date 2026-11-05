"""적용 예외 규칙 표(도메인 팩의 rules_table.yaml, EC-SW는 첨부3)로 보안 항목의 적용 여부를 다시 계산한다."""

from . import pack as pack_mod

FLIP = {"적용": "N/A", "N/A": "적용"}


def load_doc(path=None, reader=None, pack=None):
    """규칙 표 문서. 경로를 안 주면 도메인 팩(기본 EC-SW)의 규칙 표."""
    if path is None:
        return pack_mod.load(pack).rules_doc(reader)
    return pack_mod.read_commented_json(path, reader)


def load_table(path=None, reader=None, pack=None):
    return load_doc(path, reader, pack)["rules"]


def resolve_attr(attr, attr_ko, table):
    """요청의 속성 키·이름을 규칙 표의 속성으로. 키가 같으면 그것, 아니면 이름에 별칭 낱말이 든 첫 규칙. 없으면 None."""
    if any(r["attr"] == attr for r in table):
        return attr
    for r in table:
        if any(a and a in (attr_ko or "") for a in r.get("aliases", [])):
            return r["attr"]
    return None


def sec_clauses(table):
    """보안 항목 → 근거 조항 ID(규칙 표의 clause_ids)."""
    out = {}
    for r in table:
        for s in r["secs"]:
            out.setdefault(s, []).extend(r.get("clause_ids", []))
    return out


def rule_secs(attr, table):
    return [s for r in table if r["attr"] == attr for s in r["secs"]]


def applicability(attrs, table):
    """{보안 항목: 적용|N/A} (규칙이 정하는 항목만)."""
    out = {}
    for r in table:
        if r["attr"] not in attrs:
            continue
        status = r["result"] if attrs[r["attr"]] == r["equals"] else FLIP[r["result"]]
        for s in r["secs"]:
            out[s] = status
    return out


def flips(attr, old, new, attrs, table):
    """속성을 old→new로 바꿀 때 적용 여부가 뒤집히는 항목."""
    before = applicability({**attrs, attr: old}, table)
    after = applicability({**attrs, attr: new}, table)
    rid = {s: r["id"] for r in table for s in r["secs"]}
    return [{"sec": s, "before": before[s], "after": after[s], "rule": rid[s]}
            for s in rule_secs(attr, table) if before.get(s) != after.get(s)]


def flips_many(changes, attrs, table):
    """속성 여러 개를 함께 바꿀 때(changes=[(attr, old, new), …]) 적용 여부가 뒤집히는 항목. 속성 하나면 flips와 같다."""
    if len(changes) == 1:
        return flips(*changes[0], attrs, table)
    before = applicability({**attrs, **{a: o for a, o, _ in changes}}, table)
    after = applicability({**attrs, **{a: n for a, _, n in changes}}, table)
    rid = {s: r["id"] for r in table for s in r["secs"]}
    out, seen = [], set()
    for a, _, _ in changes:
        for s in rule_secs(a, table):
            if s not in seen and before.get(s) != after.get(s):
                seen.add(s)
                out.append({"sec": s, "before": before[s], "after": after[s], "rule": rid[s]})
    return out


def infer_attrs(status, table):
    """문서의 적용 여부(보안 항목 → 적용|N/A)에서 속성 값을 거꾸로 읽는다. 항목이 엇갈리면 다수결."""
    votes = {}
    for r in table:
        for s in r["secs"]:
            if s in status:  # 이 항목이 규칙대로면 속성 == equals
                votes.setdefault(r["attr"], []).append(r["equals"] if status[s] == r["result"] else not r["equals"])
    return {a: sum(v) * 2 >= len(v) for a, v in votes.items()}
