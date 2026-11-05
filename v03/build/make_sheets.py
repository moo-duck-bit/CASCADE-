"""사람이 케이스를 고를 시트 4개를 만든다(케이스는 만들지 않는다). 숫자는 문서 3종을 모두 넣었을 때 기준이다.

경로 계산은 spec.md 9절(v0.2 make_cases.path_of와 같은 정의)을 따른다.
출력: v03/products/<P>/sheets/fact_list.csv, cyber_attrs.csv, req_list.csv, case_spec.csv (Excel용 UTF-8 BOM)
"""

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from common import ALL_REQS, APP_REQS, CYBER_BASE, DOCS, PRODUCT, PROFILE, SHEETS
from datagen import NUMBER_RE, SEC_ITEMS, base_applicability, read_json, with_unit

RULE_CELLS = ("체크리스트 적용 여부", "체크리스트 입증 방법", "적용 여부", "판정")


def safe_candidates(f, surfaces):
    """(쓸 수 있는 후보, 뺀 후보). 숫자 보안 사실은 stronger_when 방향만 남기고, 숫자가 아닌 보안 후보는 사람 확인을 붙인다.
    다른 사실의 현재 표기(surfaces)와 같은 후보는 같은 값 함정과 정답이 섞이므로 표시한다."""
    keep, drop = [], []
    for c in f["change_candidates"]:
        label = with_unit(f, c)
        if label in surfaces:
            label += "(다른 사실 값과 같음)"
        numeric = NUMBER_RE.fullmatch(c) and NUMBER_RE.fullmatch(f["value"])
        if f["security"] and f["stronger_when"] in ("higher", "lower") and numeric:
            ok = (float(c) > float(f["value"])) == (f["stronger_when"] == "higher")
            (keep if ok else drop).append(label)
        elif f["security"] and f["stronger_when"] == "text":
            keep.append(label + "(강화인지 사람 확인)")
        else:
            keep.append(label)
    return keep, drop


def lower(flag):
    return str(flag).lower()


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)


def main():
    fj, lj = read_json(PRODUCT / "facts.json"), read_json(PRODUCT / "links.json")
    facts, reqs, links, rules = fj["facts"], fj["reqs"], lj["links"], lj["rules"]
    sites = read_json(DOCS / "sites.json")["sites"]
    app = base_applicability(rules, fj["cyber"])
    surfaces = {v for f in facts.values() for v in f["variants"]}
    rel = {(link["req"], link["sec"]): link["relevant"] for link in links}
    users = defaultdict(list)
    for rid, r in reqs.items():
        for fid in r["uses"]:
            users[fid].append(rid)
    fact_sites = defaultdict(list)
    for s in sites:
        for f in s["facts"]:
            fact_sites[f["fact"]].append(s)
    dependents = {fid: [g for g, f in facts.items() if fid in f["depends_on"]] for fid in facts}

    def path_of(s, origin):
        if s["doc"] == "MDSR" and s["item"] == origin:
            return "origin"
        if s["doc"] != "MDSR" and s["item"] == origin:
            return "same_id"
        if s["doc"] == "XXCS" and rel.get((origin, s["item"])) is True:
            return "trace"
        return "same_value"

    rows = [["사실", "이름", "현재 값", "보안 사실", "쓰는 Req", "들어간 칸 수(MDSR/MDDR/XXCS)", "연결된 보안 항목(놓인 XXCS 행)",
             "같은 값 짝", "바꿀 후보(보안 약화 아닌 것만)", "엮이는 문서 수",
             "출발 Req별 trace 행 수", "출발 Req별 same_value 칸 수", "파생 사실(depends_on)", "뺀 후보(보안 약화)"]]
    for fid, f in facts.items():
        ss = fact_sites[fid]
        n = Counter(s["doc"] for s in ss)
        secs = sorted({s["item"] for s in ss if s["doc"] == "XXCS"})
        sec_txt = ", ".join(f"{s}({'/'.join(r for r in users[fid] if rel.get((r, s)) is True) or 'relevant 없음'})"
                            for s in secs)
        group = f["same_value_group"]
        mates = [g for g, o in facts.items() if g != fid and group and o["same_value_group"] == group]
        keep, drop = safe_candidates(f, surfaces)
        per_trace, per_sv = [], []
        for r in users[fid]:
            all_sites = ss + [s for d in dependents[fid] for s in fact_sites[d]]
            paths = Counter(path_of(s, r) for s in {s["site"]: s for s in all_sites}.values())
            trace_rows = {s["item"] for s in ss if s["doc"] == "XXCS" and path_of(s, r) == "trace"}
            per_trace.append(f"{r}:{len(trace_rows)}")
            per_sv.append(f"{r}:{paths['same_value']}")
        rows.append([fid, f["name"], f["variants"][0], "예" if f["security"] else "아니오", ", ".join(users[fid]),
                     f"{n['MDSR']}/{n['MDDR']}/{n['XXCS']}", sec_txt or "-",
                     ", ".join(f"{m}({facts[m]['variants'][0]})" for m in mates) or "-",
                     " / ".join(keep) or "(없음)", len(n), " ".join(per_trace), " ".join(per_sv),
                     ", ".join(f["depends_on"]) or "-", " / ".join(drop) or "-"])
    write_csv(SHEETS / "fact_list.csv", rows)

    weak = PROFILE["weakening_flips"]
    rows = [["속성", "이름", "현재 값", "바꾼 값", "바꾸면 뒤집히는 보안 항목과 방향", "근거 규칙", "보안 약화 여부", "T2 사용",
             "정답 칸 수(XXCS 4종)"]]
    for a, v in CYBER_BASE.items():
        new = not v
        rs = [r for r in rules if r["if"]["attr"] == a]
        flips, n_cells = [], 0
        for r in rs:
            for s in r["then"]["secs"]:
                after = app[s][0]
                before_after = f"{after}→{'적용' if after == 'N/A' else 'N/A'}"
                flips.append(f"{s} {SEC_ITEMS[s]}: {before_after}")
                n_cells += sum(1 for x in sites
                               if x["doc"] == "XXCS" and x["item"] == s and x["cell"].split("#")[0] in RULE_CELLS)
        direction = f"{lower(v)}->{lower(new)}"
        weakening = weak.get(a) == direction
        if weakening:
            verdict = "약화(보안 기능을 끄는 방향)"
        elif any("N/A→적용" in x for x in flips):
            verdict = "약화 아님(보안 항목이 새로 적용됨)"
        else:
            verdict = "약화 아님"
        rows.append([a, PROFILE["cyber_ko"][a][0], lower(v), lower(new), "; ".join(flips),
                     "; ".join(f"{r['id']}: {r['source']}" for r in rs), verdict,
                     "쓰지 않음" if weakening else "가능", n_cells])
    write_csv(SHEETS / "cyber_attrs.csv", rows)

    rows = [["Req", "구성요소", "제목", "쓰는 사실", "연결된 보안 항목(relevant)", "연결된 보안 항목(relevant=false, 과연결)",
             "보안 요구사항", "삭제 금지", "삭제 시 trace 칸 수(MDSR 6절 + XXCS 해당 요구사항)"]]
    for rid in ALL_REQS:
        r = reqs[rid]
        rs = sorted({link["sec"] for link in links if link["req"] == rid and link["relevant"]})
        fs = sorted({link["sec"] for link in links if link["req"] == rid and not link["relevant"]})
        linked_app = [s for s in rs + fs if app[s][0] == "적용"]
        n_trace = sum(1 for x in sites if x["item"] in linked_app and (
            (x["doc"] == "MDSR" and x["cell"] == "소프트웨어 기능 요구사항 번호")
            or (x["doc"] == "XXCS" and x["cell"] == "해당 요구사항")))
        rows.append([rid, "모바일 애플리케이션" if rid in APP_REQS else "서버", r["title"], ", ".join(r["uses"]),
                     ", ".join(rs) or "-", ", ".join(fs) or "-", "예" if r["security"] else "아니오",
                     "삭제 금지" if r["security"] else "", n_trace])
    write_csv(SHEETS / "req_list.csv", rows)

    nonsec = [rid for rid in ALL_REQS if not reqs[rid]["security"]]
    def n_trace_rows(fid, r):
        return len({s["item"] for s in fact_sites[fid] if s["doc"] == "XXCS" and path_of(s, r) == "trace"})

    def clean(fid):
        return [c for c in safe_candidates(facts[fid], surfaces)[0] if "(" not in c]

    # 예시1은 기준을 그대로 따르는 것으로 고른다: 보안 사실, 출발 Req의 trace 행 2개 이상, 표시가 붙지 않은 후보
    ex_t1_id, ex_t1_req = next(((fid, max(users[fid], key=lambda r: n_trace_rows(fid, r)))
                                for fid, f in facts.items()
                                if f["security"] and clean(fid)
                                and any(n_trace_rows(fid, r) >= 2 for r in users[fid])), (None, None))
    ex_t1 = facts[ex_t1_id]
    keep = clean(ex_t1_id)
    ok_attrs = [a for a in CYBER_BASE if weak.get(a) != f"{lower(CYBER_BASE[a])}->{lower(not CYBER_BASE[a])}"]
    ex_attr = ok_attrs[0]
    ex_flip = f"{lower(CYBER_BASE[ex_attr])}→{lower(not CYBER_BASE[ex_attr])}"
    guide = [
        ["# 작성 기준 (v03/spec.md 요약). '#'으로 시작하는 줄은 설명이며 읽을 때 건너뛴다."],
        ["# 개수: T1 값 변경 24 / T2 규칙 재계산 12 / T3 요구사항 변경 24 = 추가 14(그중 복합 6: 값 변경 동반 3, 속성 변경 동반 3) + 삭제 10."],
        ["# 엮인 문서 수: 유형마다 1·2·3이 각각 1/3 ±10%p(T1·T3는 각 6~10개, T2는 각 3~5개). 같은 변경을 문서 조합만 달리해 넣어도 된다."],
        ["# 문서: T1·T3는 MDSR을 항상 포함(출발 칸). T2는 XXCS를 항상 포함(rule 정답 칸). "
         "엮인 문서 칸에는 MDSR, MDSR+XXCS, MDSR+MDDR, MDSR+MDDR+XXCS처럼 조합을 쓴다."],
        ["# T1: 12개 이상은 바뀌는 사실이 출발 Req와 relevant로 연결된 XXCS 행 2개 이상에 놓인 것"
         "(fact_list '출발 Req별 trace 행 수'가 2 이상)으로 고르고 XXCS를 포함한다."],
        ["# same_value: P1 전체 same_value 정답 칸이 35칸 이상이 되게 고른다(fact_list '출발 Req별 same_value 칸 수' 합)."],
        ["# 보안 약화 금지: req_list에서 '삭제 금지'인 Req는 삭제하지 않는다. 바꿀 값은 fact_list '바꿀 후보'에서만 고른다. "
         "cyber_attrs에서 T2 사용 '쓰지 않음'인 속성은 쓰지 않는다."],
        [f"# 삭제 후보(보안 요구사항 아님, {len(nonsec)}개): {', '.join(nonsec)}"],
        [f"# T2에 쓸 수 있는 속성: {', '.join(ok_attrs)}"],
        ["# 열: case_id / 유형(T1, T2, T3-추가, T3-추가복합, T3-삭제) / 무엇을 바꾸나(사실 id, 속성, Req) / "
         "변경(옛 값→새 값, 새 Req 제목 등) / 노리는 경로(origin, same_id, trace, same_value, rule) / 엮인 문서 수(조합) / 메모"],
    ]
    header = ["case_id", "유형", "무엇을 바꾸나", "변경", "노리는 경로", "엮인 문서 수", "메모"]
    examples = [
        ["예시1", "T1", f"{ex_t1_id} ({ex_t1['name']}), 출발 {ex_t1_req}",
         f"{ex_t1['variants'][0]}→{keep[0] if keep else '?'}",
         "origin, same_id, trace, same_value", "3 (MDSR+MDDR+XXCS)", "예시. trace 행 2개 이상인 사실"],
        ["예시2", "T2", f"{ex_attr} ({PROFILE['cyber_ko'][ex_attr][0]})", ex_flip, "rule", "1 (XXCS)",
         "예시. N/A→적용 방향"],
        ["예시3", "T3-추가복합", f"새 앱 Req. {int(APP_REQS[-1].split()[1]) + 1} + {ex_attr}",
         f"새 Req '(제목)' 추가, {ex_attr} {ex_flip}",
         "origin, same_id, trace, rule", "3 (MDSR+MDDR+XXCS)", "예시. 속성 변경 동반 복합"],
    ]
    blanks = ([[f"T1_{i:02d}", "T1", "", "", "", "", ""] for i in range(1, 25)]
              + [[f"T2_{i:02d}", "T2", "", "", "", "", ""] for i in range(1, 13)]
              + [[f"T3_{i:02d}", "", "", "", "", "", ""] for i in range(1, 25)])
    write_csv(SHEETS / "case_spec.csv", guide + [header] + examples + blanks)

    n_trace2 = sum(1 for fid in facts for r in users[fid] if n_trace_rows(fid, r) >= 2)
    print(f"wrote 4 sheets to {SHEETS}")
    print(f"facts {len(facts)}, (fact, origin Req) pairs with >=2 trace rows: {n_trace2}, "
          f"non-security Reqs {len(nonsec)}, T2-usable attrs {len(ok_attrs)}")


if __name__ == "__main__":
    sys.exit(main())
