"""v0.3 사실 표와 연결표(products/<P>/product/facts.json, links.json) 검사(v03/spec.md 3·6·7·11절).

섹션별 오류 목록을 돌려주므로 make_facts.py가 실패한 섹션만 다시 만들 수 있다.
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from common import ALL_REQS, APP_REQS, CYBER_BASE, PROD, PRODUCT, PROFILE, RULES, SERVER_REQS
from datagen import LINK_SECS, base_applicability, digits_of, read_json, real_name_hits, unit_of

T = PROFILE["targets"]
STRONGER = ("higher", "lower", "text", "none")
APPLICABLE = [s for s in LINK_SECS if base_applicability(RULES, CYBER_BASE)[s][0] == "적용"]


def check_facts_section(facts):
    errs = []
    lo, hi = T["facts"]
    if not lo <= len(facts) <= hi:
        errs.append(f"사실이 {len(facts)}개입니다. {lo}~{hi}개여야 합니다.")
    for fid, f in facts.items():
        if not re.fullmatch(r"F_[A-Z0-9_]+", fid):
            errs.append(f"{fid}: bad id")
        if len(f["variants"]) != 3 or len(set(f["variants"])) != 3:
            errs.append(f"{fid}: variants must be 3 distinct strings")
        for v in f["variants"]:
            if v in f["change_candidates"]:
                errs.append(f"{fid}: variant {v!r} equals a change_candidate")
        cands = f["change_candidates"]
        if f["value"] in cands or len(set(cands)) != len(cands):
            errs.append(f"{fid}: change_candidates must differ from value and each other")
        if not 2 <= len(cands) <= 3:
            errs.append(f"{fid}: change_candidates must be 2..3")
        for dep in f["depends_on"]:
            if dep not in facts or dep == fid:
                errs.append(f"{fid}: depends_on {dep} invalid")
        if f["stronger_when"] not in STRONGER:
            errs.append(f"{fid}: stronger_when {f['stronger_when']!r}")
        if f["security"] and f["stronger_when"] == "none":
            errs.append(f"{fid}: 보안 사실은 stronger_when이 higher/lower/text 중 하나여야 합니다.")
        if f["security"] and f["stronger_when"] in ("higher", "lower") and f["value"].replace(".", "").isdigit():
            ok = [c for c in cands if c.replace(".", "").isdigit()
                  and (float(c) > float(f["value"])) == (f["stronger_when"] == "higher")]
            if not ok:
                errs.append(f"{fid}: 보안을 약하게 하지 않는 변경 후보가 하나도 없습니다"
                            f"(stronger_when={f['stronger_when']}).")
    groups = defaultdict(list)
    for fid, f in facts.items():
        if f["same_value_group"]:
            groups[f["same_value_group"]].append(fid)
    pairs = 0
    for g, members in groups.items():
        if len(members) < 2:
            errs.append(f"group {g}: only one member {members}")
        if len({facts[m]['value'] for m in members}) != 1:
            errs.append(f"group {g}: values differ")
        if len({unit_of(facts[m]) for m in members}) != 1:
            errs.append(f"group {g}: units differ (단위까지 같은 짝만 같은 값 묶음)")
        pairs += len(members) * (len(members) - 1) // 2
    if pairs < T["same_value_pairs"]:
        errs.append(f"같은 값 짝이 {pairs}쌍입니다. {T['same_value_pairs']}쌍 이상이어야 합니다.")
    owner = {}
    for fid, f in facts.items():
        for v in {f["value"], *f["variants"]}:
            if v in owner and facts[owner[v]]["same_value_group"] != f["same_value_group"]:
                errs.append(f"surface {v!r} shared by {owner[v]} and {fid} outside one same_value_group")
            owner.setdefault(v, fid)
    by_value = defaultdict(list)
    for fid, f in facts.items():
        by_value[f["value"]].append(fid)
    for val, fids in by_value.items():
        gs = {facts[x]["same_value_group"] for x in fids}
        if len(fids) > 1 and (len(gs) != 1 or "" in gs):
            errs.append(f"value {val!r} shared by {fids} without one same_value_group")
    for label, keys in PROFILE["required_security"].items():
        if not any(all(k in f["name"] for k in keys) for f in facts.values()):
            errs.append(f"required security fact missing: {label}")
    errs += [f"facts: real name {n!r}" for n in real_name_hits(json.dumps(facts, ensure_ascii=False))]
    return errs


def check_placement(facts):
    """XXCS 배치와 3.4 배치(facts[*].xxcs, in_34)."""
    errs = []
    per_sec = Counter()
    for fid, f in facts.items():
        for s in f["xxcs"]:
            if s not in APPLICABLE:
                errs.append(f"{fid}: {s}는 기본 상태에서 적용 항목이 아닙니다(쓸 수 있는 항목만).")
            per_sec[s] += 1
        if len(set(f["xxcs"])) != len(f["xxcs"]):
            errs.append(f"{fid}: xxcs 중복")
        if f["security"] and not f["xxcs"]:
            errs.append(f"{fid}: 보안 사실은 XXCS 항목이 1개 이상이어야 합니다.")
        if f["in_34"] and not (f["security"] and f["xxcs"]):
            errs.append(f"{fid}: in_34는 XXCS 항목이 있는 보안 사실만 true입니다.")
    for s, c in per_sec.items():
        if c > 4:
            errs.append(f"{s}: 사실이 {c}개 배치됐습니다. 한 항목에는 4개 이하로 두세요.")
    n2 = sum(1 for f in facts.values() if len(f["xxcs"]) >= 2)
    if n2 < T["trace2_facts"]:
        errs.append(f"XXCS 항목 2개 이상에 놓인 사실이 {n2}개입니다. {T['trace2_facts']}개 이상이어야 합니다.")
    n34 = sum(1 for f in facts.values() if f["in_34"])
    if n34 < 5:
        errs.append(f"in_34 사실이 {n34}개입니다. 5개 이상이어야 합니다.")
    return errs


def users_of(reqs):
    out = defaultdict(list)
    for rid, r in reqs.items():
        for fid in r["uses"]:
            out[fid].append(rid)
    return out


def check_reqs_section(reqs, facts, fixed_titles):
    errs = []
    if sorted(reqs) != sorted(ALL_REQS):
        errs.append(f"reqs must be exactly {APP_REQS[0]}..{APP_REQS[-1]}, {SERVER_REQS[0]}..{SERVER_REQS[-1]}")
    for rid, title in fixed_titles.items():
        if rid in reqs and reqs[rid]["title"] != title:
            errs.append(f"{rid}: 제목은 '{title}' 그대로 두세요.")
    # v0.2 제목에는 앱·서버가 같은 이름이 있다('전송 암호화', '데이터 백업'). 새 Req 제목만 겹치지 않게 본다.
    titles = Counter(r["title"] for r in reqs.values())
    errs += [f"{rid}: 제목 '{r['title']}'이 다른 Req와 겹칩니다. 다르게." for rid, r in reqs.items()
             if rid not in fixed_titles and titles[r["title"]] > 1]
    used = Counter()
    for rid, r in reqs.items():
        if not 1 <= len(r["uses"]) <= 3:
            errs.append(f"{rid}: uses가 {len(r['uses'])}개입니다. 1~3개로.")
        for fid in r["uses"]:
            if fid not in facts:
                errs.append(f"{rid}: unknown fact {fid}")
            used[fid] += 1
        sec_uses = [u for u in r["uses"] if u in facts and facts[u]["security"]]
        if sec_uses and not r["security"]:
            errs.append(f"{rid}: 보안 사실({', '.join(sec_uses)})을 쓰므로 security=true여야 합니다. "
                        "기능 요구사항이면 보안 사실을 빼세요.")
    room = [rid for rid, r in reqs.items() if len(r["uses"]) < 3]
    for fid in facts:
        if used[fid] == 0:
            errs.append(f"{fid}: 어느 Req도 쓰지 않습니다. "
                        f"uses가 3개 미만인 Req({', '.join(room[:6]) or '없음'}) 중 하나에 추가하세요.")
    multi = sum(1 for fid in facts if used[fid] >= 2)
    if multi < T["multi_req_facts"]:
        singles = [f"{fid}({facts[fid]['name']})" for fid in facts if used[fid] == 1][:12]
        errs.append(f"Req 2개 이상이 함께 쓰는 사실이 {multi}개입니다. {T['multi_req_facts']}개 이상이어야 합니다. "
                    f"지금 한 Req만 쓰는 사실 중 {T['multi_req_facts'] - multi + 1}개 이상을 "
                    f"그 값을 실제로 참조하는 다른 Req(uses가 3개 미만: {', '.join(room[:10])})의 uses에 추가하세요"
                    f"(보안 사실은 security=true인 Req에만). 후보: {', '.join(singles)}")
    nonsec = [rid for rid, r in reqs.items() if not r["security"]]
    if len(nonsec) < T["nonsec_reqs"]:
        errs.append(f"보안 요구사항이 아닌 Req가 {len(nonsec)}개입니다. {T['nonsec_reqs']}개 이상이어야 합니다.")
    errs += [f"reqs: real name {n!r}" for n in real_name_hits(json.dumps(reqs, ensure_ascii=False))]
    return errs


def check_links_section(links, reqs, facts):
    errs = []
    secs = Counter(link["sec"] for link in links)
    errs += [f"{s}: no linked Req" for s in LINK_SECS if secs[s] == 0]
    pairs = Counter((link["req"], link["sec"]) for link in links)
    errs += [f"duplicate link {r}-{s}" for (r, s), c in pairs.items() if c > 1]
    n_false = sum(1 for link in links if not link["relevant"])
    rate = n_false / max(1, len(links))
    if abs(rate - T["overlink_rate"]) > 0.02:
        errs.append(f"relevant=false rate {rate:.3f} != {T['overlink_rate']}")
    rel = {(link["req"], link["sec"]) for link in links if link["relevant"]}
    users = users_of(reqs)
    for fid, f in facts.items():
        for s in f["xxcs"]:
            if not any((rid, s) in rel for rid in users[fid]):
                errs.append(f"{fid}: {s}에 놓였지만 이 사실을 쓰는 Req 중 {s}와 relevant로 연결된 것이 없음")
    linked = Counter(link["req"] for link in links)
    nonsec_linked = [rid for rid, r in reqs.items() if not r["security"] and linked[rid] >= 1]
    if len(nonsec_linked) < T["nonsec_linked_reqs"]:
        errs.append(f"연결이 있는 비보안 Req가 {len(nonsec_linked)}개입니다. "
                    f"{T['nonsec_linked_reqs']}개 이상이어야 합니다.")
    return errs


def check_rules():
    errs = []
    seen = Counter(s for r in RULES for s in r["then"]["secs"])
    errs += [f"{s}: governed by more than one rule" for s, c in seen.items() if c > 1]
    for r in RULES:
        if r["if"]["attr"] not in CYBER_BASE:
            errs.append(f"{r['id']}: unknown attr")
        errs += [f"{r['id']}: unknown sec {s}" for s in r["then"]["secs"] if s not in LINK_SECS]
    attrs = {r["if"]["attr"] for r in RULES}
    if attrs != set(CYBER_BASE):
        errs.append(f"규칙이 없는 속성: {sorted(set(CYBER_BASE) - attrs)}")
    weak = PROFILE["weakening_flips"]
    flips = [a for a in CYBER_BASE if f"{str(CYBER_BASE[a]).lower()}->{str(not CYBER_BASE[a]).lower()}" != weak.get(a)]
    if len(flips) < 4:
        errs.append(f"보안 약화가 아닌 T2 방향이 {len(flips)}개뿐")
    return errs


def fixed_titles():
    """v0.2에서 이어받은 Req 제목(제품 seed 파일)."""
    v02 = read_json((PROD / PROFILE["seed_facts_from"]).resolve())
    return {rid: r["title"] for rid, r in v02["reqs"].items() if rid in ALL_REQS}


def run_all(fj, lj):
    return {
        "facts": check_facts_section(fj["facts"]),
        "placement": check_placement(fj["facts"]),
        "reqs": check_reqs_section(fj["reqs"], fj["facts"], fixed_titles()),
        "links": check_links_section(lj["links"], fj["reqs"], fj["facts"]),
        "rules": check_rules(),
    }


def fact_warnings(facts):
    out = []
    for fid, f in facts.items():
        d = digits_of(f["value"])
        for v in f["variants"]:
            if digits_of(v) != d:
                out.append(f"{fid}: variant {v!r} vs value {f['value']!r} (숫자 다름, 사람 확인)")
    return out


def main():
    fj, lj = read_json(PRODUCT / "facts.json"), read_json(PRODUCT / "links.json")
    ok = True
    for section, errs in run_all(fj, lj).items():
        print(f"[{'PASS' if not errs else 'FAIL'}] {section}: {len(errs)} error(s)")
        for e in errs:
            print("   -", e)
        ok &= not errs
    for w in fact_warnings(fj["facts"]):
        print("   [WARN]", w)
    rate = sum(1 for link in lj["links"] if not link["relevant"]) / len(lj["links"])
    print(f"relevant=false rate {rate:.3f} (target {T['overlink_rate']}, 미보정)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
