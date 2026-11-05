"""v0.3 P1 케이스 180개 검사: 정답 스키마, 위치 존재, 입력 누출, 시트와의 일치, spec.md 규격(개수·문서 수 균형·경로·보안 약화).

v02/build/check_cases.py 의 스키마·누출 검사를 옮기고 v0.3 규격 검사를 더했다.
출력: v03/runs/P1/check_cases.json, distribution.md, case_review.csv(사람 검수용)
"""

import csv
import json
import math
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from common import DOCS, PRODUCT, PROFILE, RUNS, V03
from datagen import ROOT, read_json, write_json
from make_cases import CASES, SHEET, read_sheet

PATHS = {"origin", "same_id", "trace", "same_value", "rule"}
OPS = {"modify", "insert"}
EXPECTED_INPUT = {"site_index.md", "request.txt", "request_nl.txt", "MDSR.docx", "MDDR.docx", "XXCS.docx"}
LEAK_PATTERNS = [r"\{\{", r"\}\}", r"\bF_[A-Z0-9_]{3,}", "same_value_group", "sites_hint", "change_candidates",
                 "depends_on", "impact\\.json", "must_not_touch", "\\bsame_id\\b", "\\bsame_value\\b", "\\brelevant\\b",
                 "\"substitutions\"", "rule_source", "연결 예정", "본문에 넣을 보안 요소"]
EXPECTED_COUNTS = {"T1": 72, "T2": 36, "T3": 72}
NUM_RE = re.compile(r"^\d+(\.\d+)?$")


def check_schema(cid, meta, imp, mnt, site_set):
    errs = []
    if imp.get("case_id") != cid or mnt.get("case_id") != cid:
        errs.append("case_id mismatch")
    if imp.get("case_type") not in ("T1", "T2", "T3"):
        errs.append("bad case_type")
    if not imp.get("items"):
        errs.append("empty impact")
    docs = set(meta["docs"])
    for e in imp["items"]:
        for k in ("site", "doc", "item", "cell", "path", "op"):
            if k not in e:
                errs.append(f"impact missing {k}: {e.get('site')}")
        if e.get("path") not in PATHS:
            errs.append(f"bad path {e.get('path')}")
        if e.get("op") not in OPS:
            errs.append(f"bad op {e.get('op')}")
        if e.get("doc") not in docs:
            errs.append(f"impact site outside case docs: {e['site']}")
        if e.get("site") != f"[{e.get('doc')} | {e.get('item')} | {e.get('cell')}]":
            errs.append(f"site string mismatch: {e.get('site')}")
        # op=insert는 아직 없는 위치라 자신은 없고 anchor는 있어야 한다.
        if e.get("op") == "insert":
            if e["site"] in site_set:
                errs.append(f"insert site already exists: {e['site']}")
            if e.get("anchor") not in site_set:
                errs.append(f"insert anchor missing: {e.get('anchor')}")
            if not e.get("new_value"):
                errs.append(f"insert without new_value: {e['site']}")
        elif e.get("site") not in site_set:
            errs.append(f"impact site not in sites.json: {e.get('site')}")
        if imp["case_type"] == "T2" and (e.get("path") != "rule" or not e.get("rule_source")):
            errs.append(f"T2 item needs path=rule and rule_source: {e['site']}")
        if imp["case_type"] == "T1" and not e.get("fact"):
            errs.append(f"T1 item needs fact: {e['site']}")
    for e in mnt["items"]:
        for k in ("site", "path", "reason"):
            if k not in e:
                errs.append(f"trap missing {k}")
        if e.get("site") not in site_set:
            errs.append(f"trap site not in sites.json: {e.get('site')}")
        if e.get("path") not in PATHS - {"origin"}:
            errs.append(f"bad trap path {e.get('path')}")
        if e.get("doc") and e["doc"] not in docs:
            errs.append(f"trap outside case docs: {e['site']}")
    impact_sites = [e["site"] for e in imp["items"]]
    trap_sites = {e["site"] for e in mnt["items"]}
    neutral = {e["site"] for e in imp.get("neutral", [])}
    if len(impact_sites) != len(set(impact_sites)):
        errs.append("duplicate impact sites")
    if neutral - site_set:
        errs.append(f"neutral site not in sites.json: {sorted(neutral - site_set)[:3]}")
    if neutral & (set(impact_sites) | trap_sites):
        errs.append("neutral overlaps impact or must_not_touch")
    if set(impact_sites) & trap_sites:
        errs.append(f"impact ∩ must_not_touch: {sorted(set(impact_sites) & trap_sites)[:3]}")
    if meta["n_impact"] != len(imp["items"]) or meta["n_traps"] != len(mnt["items"]):
        errs.append("meta counts mismatch")
    return errs


def docx_text(path):
    z = zipfile.ZipFile(path)
    out = []
    for n in z.namelist():
        if n.startswith("word/") and n.endswith(".xml"):
            out.append("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", z.read(n).decode("utf-8", "replace"))))
    return "\n".join(out)


def check_leak(cid, facts):
    """입력 폴더에 사실 표·위치 표 내용, {{ }} 흔적, 정답 파일명·경로 라벨, 시트 메모 문구가 없는지."""
    errs = []
    inp = CASES / cid / "input"
    names = {p.name for p in inp.iterdir()}
    if names - EXPECTED_INPUT:
        errs.append(f"unexpected input files {sorted(names - EXPECTED_INPUT)}")
    for p in inp.iterdir():
        text = docx_text(p) if p.suffix == ".docx" else p.read_text(encoding="utf-8")
        for pat in LEAK_PATTERNS:
            if re.search(pat, text):
                errs.append(f"{p.name}: leak pattern {pat!r}")
        for fid in facts:
            if fid in text:
                errs.append(f"{p.name}: fact id {fid}")
    return errs


def import_check():
    """생성 코드가 document_ai 패키지를 import 하는 줄."""
    bad = []
    for p in list((V03 / "build").glob("*.py")) + list((ROOT / "lib").glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*(from|import)\s+document_ai\b", line):
                bad.append(f"{p.name}:{i}: {line.strip()}")
    return bad


def weakening(fact, old, new):
    """보안 사실을 stronger_when 반대로 바꾸면 True. 숫자가 아니면 판단하지 않는다(None)."""
    if not fact["security"]:
        return False
    if not (NUM_RE.match(old) and NUM_RE.match(new)):
        return None
    up = float(new) > float(old)
    return up != (fact["stronger_when"] == "higher")


def balance_range(n):
    return math.ceil(n * (1 / 3 - 0.1) - 1e-9), math.floor(n * (1 / 3 + 0.1) + 1e-9)


def distribution_md(index, dist, item_dist):
    header = ["| 유형 | 경로 | n_docs=1 | n_docs=2 | n_docs=3 | 합계 |", "|---|---|---:|---:|---:|---:|"]
    lines = ["# 분포 표 (v0.3 P1, 180개)", "", "## 유형 × 경로 × n_docs (해당 경로가 정답에 있는 케이스 수)", "", *header]
    for (t, p) in sorted(dist):
        c = dist[(t, p)]
        lines.append(f"| {t} | {p} | {c[1]} | {c[2]} | {c[3]} | {sum(c.values())} |")
    lines += ["", "## 유형 × 경로 × n_docs (정답 위치 수)", "", *header]
    for (t, p) in sorted(item_dist):
        c = item_dist[(t, p)]
        lines.append(f"| {t} | {p} | {c[1]} | {c[2]} | {c[3]} | {sum(c.values())} |")
    case_counts = Counter((m["case_type"], m["n_docs"]) for m in index)
    lines += ["", "## 유형 × n_docs (케이스 수)", "", "| 유형 | 1 | 2 | 3 | 합계 |", "|---|---:|---:|---:|---:|"]
    for t in ("T1", "T2", "T3"):
        counts = [case_counts[(t, n)] for n in (1, 2, 3)]
        lines.append(f"| {t} | {counts[0]} | {counts[1]} | {counts[2]} | {sum(counts)} |")
    sub = Counter((m["case_type"], m["subtype"]) for m in index)
    lines += ["", "## 세부 유형", "", "| 유형 | 세부 | 케이스 수 |", "|---|---|---:|"]
    lines += [f"| {t} | {s} | {n} |" for (t, s), n in sorted(sub.items())]
    traps = Counter(e for m in index for e, c in m["trap_paths"].items() for _ in range(c))
    lines += ["", f"함정 위치 경로별 합계: {dict(traps)}", ""]
    return "\n".join(lines)


def main():
    fj = read_json(PRODUCT / "facts.json")
    facts, reqs = fj["facts"], fj["reqs"]
    site_set = {s["site"] for s in read_json(DOCS / "sites.json")["sites"]}
    app = read_json(PRODUCT / "content.json")["applicability"]
    weak_flips = {(a, str(v).lower()) for a, v in PROFILE["weakening_flips"].items()} \
        if isinstance(PROFILE["weakening_flips"], dict) else {(a, None) for a in PROFILE["weakening_flips"]}
    index = read_json(CASES / "index.json")
    sheet = {r["case_id"]: r for r in read_sheet(SHEET)}
    errs = defaultdict(list)
    dist, item_dist = defaultdict(Counter), defaultdict(Counter)
    review_rows, seen_inputs = [], {}
    trace2_t1, sv_cells, compound = 0, 0, Counter()
    for meta in index:
        cid = meta["case_id"]
        imp = read_json(CASES / cid / "gold" / "impact.json")
        mnt = read_json(CASES / cid / "gold" / "must_not_touch.json")
        errs[cid] += check_schema(cid, meta, imp, mnt, site_set) + check_leak(cid, facts)
        key = (tuple(meta["docs"]), meta["request_structured"])
        if key in seen_inputs:
            errs[cid].append(f"same docs and request as {seen_inputs[key]}")
        seen_inputs[key] = cid
        want = {p.strip() for p in sheet[cid]["노리는 경로"].split(",")}
        if want != set(meta["paths"]):
            errs[cid].append(f"sheet paths {sorted(want)} != gold paths {sorted(meta['paths'])}")
        if meta["case_type"] in ("T1", "T3") and "MDSR" not in meta["docs"]:
            errs[cid].append("T1/T3 must include MDSR")
        if meta["case_type"] == "T2" and "XXCS" not in meta["docs"]:
            errs[cid].append("T2 must include XXCS")
        ch = imp["change"]
        value_changes = [ch] if meta["case_type"] == "T1" else [ch["value_change"]] if "value_change" in ch else []
        for vc in value_changes:
            if weakening(facts[vc["fact"]], vc["old_value"], vc["new_value"]):
                errs[cid].append(f"security weakening: {vc['fact']} {vc['old_value']}->{vc['new_value']}")
        attr_changes = ([ch] if "attr" in ch else ch.get("attrs", [])) if meta["case_type"] == "T2" else \
            ([ch["attr_change"]] if "attr_change" in ch else [])
        attr_changes = [a for c in attr_changes for a in (c.get("attrs") or [c])]
        for ac in attr_changes:
            if ac["attr"] in {a for a, _ in weak_flips}:
                errs[cid].append(f"weakening attribute flip: {ac['attr']}")
        if imp["subtype"] == "delete" and reqs[ch["deleted_req"]]["security"]:
            errs[cid].append(f"security requirement deleted: {ch['deleted_req']}")
        if imp["subtype"].startswith("add"):
            compound[imp["subtype"]] += 1
            expected = "Req. 19" if ch["component"] == "앱" else "Req. 109"
            if ch["new_req"] != expected:
                errs[cid].append(f"new req {ch['new_req']} != {expected}")
            bad = [s for s in ch["related_secs"] if app[s]["status"] != "적용"]
            if bad:
                errs[cid].append(f"link target not 적용: {bad}")
        if meta["case_type"] == "T1":
            trace_rows = {e["item"] for e in imp["items"] if e["path"] == "trace" and e.get("fact") == ch["fact"]}
            trace2_t1 += len(trace_rows) >= 2
        sv_cells += sum(1 for e in imp["items"] if e["path"] == "same_value")
        for p in {e["path"] for e in imp["items"]}:
            dist[(meta["case_type"], p)][meta["n_docs"]] += 1
        for e in imp["items"]:
            item_dist[(meta["case_type"], e["path"])][meta["n_docs"]] += 1
        review_rows.append([cid, meta["case_type"], meta["subtype"], meta["n_docs"], ",".join(meta["docs"]),
                            meta["request_structured"], meta["request_nl"],
                            "\n".join(f"{e['site']} ({e['path']})" for e in imp["items"]),
                            "\n".join(f"{e['site']} ({e['path']}) - {e['reason']}" for e in mnt["items"]),
                            sheet[cid]["메모"], ""])
    errs = {k: v for k, v in errs.items() if v}
    by_type = Counter(m["case_type"] for m in index)
    ndocs = {t: Counter(m["n_docs"] for m in index if m["case_type"] == t) for t in EXPECTED_COUNTS}
    balance = {}
    for t, c in ndocs.items():
        lo, hi = balance_range(by_type[t])
        balance[t] = all(lo <= c[n] <= hi for n in (1, 2, 3))
    imports = import_check()
    path_cover = {t: sorted({p for (tt, p) in dist if tt == t}) for t in EXPECTED_COUNTS}

    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / "distribution.md").write_text(distribution_md(index, dist, item_dist), encoding="utf-8")
    with (RUNS / "case_review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "유형", "세부", "n_docs", "문서", "요청(구조화)", "요청(자연어)", "정답 위치(경로)",
                    "함정(경로) - 이유", "시트 메모", "검수 메모"])
        w.writerows(review_rows)
    result = {
        "n_cases": len(index),
        "by_type": dict(by_type),
        "n_docs_by_type": {t: dict(sorted(c.items())) for t, c in ndocs.items()},
        "t3_add_subtypes": dict(compound),
        "t1_cases_with_2plus_trace_rows": trace2_t1,
        "same_value_gold_cells": sv_cells,
        "path_labels_by_type": path_cover,
        "errors": errs,
        "document_ai_imports": imports,
    }
    result["gate"] = {
        "counts_72_36_72": dict(by_type) == EXPECTED_COUNTS,
        "no_errors": not errs,
        "no_document_ai_import": not imports,
        "n_docs_balance_within_10pp": all(balance.values()),
        "t1_trace_cases_ge_half": trace2_t1 >= by_type["T1"] / 2,
        "same_value_cells_ge_105": sv_cells >= 105,
        "compound_9_value_9_attr": compound["add_with_value_change"] == 9 and compound["add_with_attr_change"] == 9,
        "all_paths_present": {"origin", "same_id", "trace", "same_value", "rule"} <= {p for v in path_cover.values()
                                                                                    for p in v},
    }
    result["passed"] = all(result["gate"].values())
    write_json(RUNS / "check_cases.json", result)
    keys = ("n_cases", "by_type", "n_docs_by_type", "t3_add_subtypes", "t1_cases_with_2plus_trace_rows",
            "same_value_gold_cells", "path_labels_by_type", "gate", "passed")
    print(json.dumps({k: result[k] for k in keys}, ensure_ascii=False, indent=1))
    for cid, e in list(errs.items())[:8]:
        print("ERR", cid, e[:3])
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
