"""run1·run2 예측 채점. 계산 방식은 reports/decision_criteria.md 를 따른다.

케이스 읽기, 위치 표기 파싱, 예측 위치 정규화(norm_edit)도 여기 두고 다른 폴더의 코드가 import 해서 쓴다.
사용: python scoring/score.py [--methods v1,B3,B1,B2] [--out results/run1]
"""
import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "v02" / "cases"
RUN1_PRED = ROOT / "results" / "run1" / "predictions"
RUN2_PRED = ROOT / "results" / "run2" / "predictions"
RUN1_METHODS = ("v1", "B1", "B2", "B3")
SEED = 20261031
PATHS = ("origin", "same_id", "trace", "same_value", "rule")
LEAD_RE = re.compile(r"^\s*\[(MDSR|MDDR|XXCS)\s*\|\s*([^|\]]+?)\s*\|\s*([^\]]+?)\s*\]")
LINE_RE = re.compile(r"^\[(MDSR|MDDR|XXCS) \| ([^|\]]+?) \| ([^\]]+?)\]\s?(.*)$")
SITE_RE = re.compile(r"^\[(MDSR|MDDR|XXCS) \| (.+?) \| (.+?)\]$")
SEC_RE = re.compile(r"^[A-Z]{2,3}-\d{2}$")


def load_cases():
    """case_id → index.json 메타."""
    return {m["case_id"]: m for m in json.loads((CASES / "index.json").read_text(encoding="utf-8"))}


def read_input(cid, name):
    return (CASES / cid / "input" / name).read_text(encoding="utf-8")


def load_gold(cid):
    """(impact.json, must_not_touch.json)."""
    gold = CASES / cid / "gold"
    return (json.loads((gold / "impact.json").read_text(encoding="utf-8")),
            json.loads((gold / "must_not_touch.json").read_text(encoding="utf-8")))


def parse_index(text):
    """site_index.md → {"[문서 | 항목 | 칸]": 원문}."""
    out = {}
    for ln in text.splitlines():
        m = LINE_RE.match(ln)
        if m:
            out[f"[{m.group(1)} | {m.group(2)} | {m.group(3)}]"] = m.group(4)
    return out


def case_sites(cid):
    return list(parse_index(read_input(cid, "site_index.md")))


def split_site(site):
    m = SITE_RE.match(site)
    return m.groups() if m else ("", "", "")


def item_of(site):
    m = SITE_RE.match(site)
    return f"{m.group(1)}|{m.group(2)}" if m else site


def norm_edit(e):
    """예측 하나 → 칸 위치. 어느 필드에든 머리표가 통째로 들어 있으면 그 앞 머리표를 쓴다."""
    for key in ("field", "item", "doc"):
        m = LEAD_RE.match(str(e.get(key) or ""))
        if m:
            return f"[{m.group(1)} | {m.group(2)} | {m.group(3)}]"
    # doc | item | field 를 이어 붙인 뒤 앞의 세 부분만 읽는다
    # (예: item "Req. 1 | 설명", field "본문" → [MDSR | Req. 1 | 설명], field "본문#p3 | 본문" → 본문#p3)
    joined = " | ".join(str(e.get(k) or "").strip("[] ") for k in ("doc", "item", "field"))
    parts = [x.strip() for x in joined.split("|") if x.strip()]
    if len(parts) < 3 or parts[0].upper() not in ("MDSR", "MDDR", "XXCS"):
        return None
    field = LEAD_RE.sub("", parts[2]).strip() or parts[2]
    return f"[{parts[0].upper()} | {parts[1]} | {field}]"


def load_pred(method, cid):
    pred_dir = RUN1_PRED if method in RUN1_METHODS else RUN2_PRED
    p = pred_dir / method / f"{cid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def prf(pred, gold):
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gold) if gold else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "tp": tp,
            "n_pred": len(pred), "n_gold": len(gold)}


def score_case(method, cid, meta, value_aware=False):
    rec = load_pred(method, cid)
    if rec is None:
        return None
    imp, mnt = load_gold(cid)
    gold = {e["site"]: e["path"] for e in imp["items"]}
    traps = {e["site"]: e["path"] for e in mnt["items"]}
    neutral = {e["site"] for e in imp.get("neutral", [])}
    sites = case_sites(cid)
    pred, invalid, item_level = set(), [], 0
    for e in rec.get("edits", []):
        if e.get("level") == "item":  # v1: 항목 단위 예측 → 그 항목의 모든 칸
            item_level += 1
            if value_aware and e["doc"] == "XXCS":
                continue
            key = f"{e['doc']}|{e['item']}"
            pred |= {s for s in sites if item_of(s) == key}
            continue
        s = norm_edit(e)
        if s is None:
            invalid.append(e)
        elif not (value_aware and s.startswith("[XXCS")):
            pred.add(s)
    exists = set(sites) | set(gold)  # 추가(insert) 정답 위치는 문서에 없어도 유효
    nonexist = sorted(pred - exists)
    gset, tset = set(gold), set(traps)
    neutral_pred = pred & neutral
    pred = pred - neutral  # neutral 칸은 채점에서 뺀다(정답도 함정도 아님, 따로 셈)
    cell = prf(pred, gset)
    gitems = {item_of(s) for s in gset}
    titems = {item_of(s) for s in tset} - gitems
    nitems = {item_of(s) for s in neutral} - gitems - titems
    # 항목 단위는 '항목을 찾았는가'라서 neutral 칸 수정도 그 항목을 찾은 것으로 본다. neutral만 있는 항목은 뺀다.
    pitems = {item_of(s) for s in pred | neutral_pred} - nitems
    item = prf(pitems, gitems)
    hidden = sorted(s for s in pred if item_of(s) in gitems and s not in gset and s not in neutral)
    by_path_gold = defaultdict(set)
    for s, p in gold.items():
        by_path_gold[p].add(s)
    by_path_trap = defaultdict(set)
    for s, p in traps.items():
        by_path_trap[p].add(s)
    return {
        "method": method, "case_id": cid, "case_type": meta["case_type"], "n_docs": meta["n_docs"],
        "cell": cell, "item": item,
        "trap_rate_cell": len(pred & tset) / len(pred) if pred else 0.0,
        "trap_rate_item": len(pitems & titems) / len(pitems) if pitems else 0.0,
        "trap_hits": sorted(pred & tset),
        "extra_cells": sorted(pred - gset - tset - neutral),
        "neutral_cells": sorted(neutral_pred),
        "extra_items": sorted(pitems - gitems - titems - nitems),
        "hidden_overedit_cells": hidden, "hidden_overedit_trap_cells": [s for s in hidden if s in tset],
        "path_gold": {p: len(v) for p, v in by_path_gold.items()},
        "path_tp": {p: len(v & pred) for p, v in by_path_gold.items()},
        "trap_path_total": {p: len(v) for p, v in by_path_trap.items()},
        "trap_path_hit": {p: len(v & pred) for p, v in by_path_trap.items()},
        "missed": sorted(gset - pred), "invalid_edits": len(invalid), "nonexistent_sites": nonexist,
        "item_level_preds": item_level, "n_pred_raw": len(rec.get("edits", [])),
        "input_tokens": rec.get("input_tokens") or 0, "output_tokens": rec.get("output_tokens") or 0,
        "seconds": rec.get("seconds") or 0.0, "parse_ok": rec.get("parse_ok"), "truncated": rec.get("truncated"),
        "error": rec.get("error"),
    }


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def boot_ci(vals, rng, n=1000):
    """케이스 복원 추출 부트스트랩 평균의 2.5%·97.5% 분위."""
    if not vals:
        return None
    means = sorted(mean(rng.choices(vals, k=len(vals))) for _ in range(n))
    return [means[int(0.025 * n)], means[int(0.975 * n) - 1]]


def summarize(rs):
    return {"n": len(rs),
            "item_f1": mean(r["item"]["f1"] for r in rs), "cell_f1": mean(r["cell"]["f1"] for r in rs),
            "item_recall": mean(r["item"]["recall"] for r in rs),
            "cell_recall": mean(r["cell"]["recall"] for r in rs),
            "item_precision": mean(r["item"]["precision"] for r in rs),
            "cell_precision": mean(r["cell"]["precision"] for r in rs),
            "trap_rate_cell": mean(r["trap_rate_cell"] for r in rs),
            "trap_rate_item": mean(r["trap_rate_item"] for r in rs),
            "extra_cells": mean(len(r["extra_cells"]) for r in rs),
            "neutral_cells": mean(len(r["neutral_cells"]) for r in rs),
            "tokens_in": mean(r["input_tokens"] for r in rs), "tokens_out": mean(r["output_tokens"] for r in rs),
            "seconds": mean(r["seconds"] for r in rs),
            "empty_pred": sum(1 for r in rs if r["cell"]["n_pred"] == 0),
            "parse_fail": sum(1 for r in rs if r["parse_ok"] is False),
            "truncated": sum(1 for r in rs if r["truncated"]),
            "nonexistent_sites": sum(len(r["nonexistent_sites"]) for r in rs)}


def aggregate(rows):
    rng = random.Random(SEED)
    out = {"by_type": {}, "by_path": {}, "by_ndocs": {}, "overall": {}, "hidden_overedit": {}}
    for m in sorted({r["method"] for r in rows}):
        mr = [r for r in rows if r["method"] == m]
        out["overall"][m] = summarize(mr)
        out["by_type"][m] = {}
        for t in ("T1", "T2", "T3"):
            tr = [r for r in mr if r["case_type"] == t]
            s = summarize(tr)
            s["item_f1_ci"] = boot_ci([r["item"]["f1"] for r in tr], rng)
            s["cell_f1_ci"] = boot_ci([r["cell"]["f1"] for r in tr], rng)
            out["by_type"][m][t] = s
        out["by_path"][m] = {}
        for p in PATHS:
            g = sum(r["path_gold"].get(p, 0) for r in mr)
            tp = sum(r["path_tp"].get(p, 0) for r in mr)
            tt = sum(r["trap_path_total"].get(p, 0) for r in mr)
            th = sum(r["trap_path_hit"].get(p, 0) for r in mr)
            out["by_path"][m][p] = {"gold": g, "recall": tp / g if g else None,
                                    "traps": tt, "trap_hit_ratio": th / tt if tt else None}
        out["by_ndocs"][m] = {}
        for n in (1, 2, 3):
            nr = [r for r in mr if r["n_docs"] == n]
            by_n = {"n": len(nr), "cell_f1": mean(r["cell"]["f1"] for r in nr),
                    "item_f1": mean(r["item"]["f1"] for r in nr),
                    "cell_f1_ci": boot_ci([r["cell"]["f1"] for r in nr], rng)}
            for t in ("T1", "T2", "T3"):
                by_n[f"{t}_cell_f1"] = mean(r["cell"]["f1"] for r in nr if r["case_type"] == t)
            out["by_ndocs"][m][n] = by_n
        with_pred = [r for r in mr if r["cell"]["n_pred"] > 0]
        hid = [r for r in with_pred if r["hidden_overedit_cells"]]
        hidt = [r for r in with_pred if r["hidden_overedit_trap_cells"]]
        out["hidden_overedit"][m] = {"cases_with_pred": len(with_pred), "cases": len(hid),
                                     "ratio": len(hid) / len(with_pred) if with_pred else None,
                                     "trap_only_cases": len(hidt),
                                     "trap_only_ratio": len(hidt) / len(with_pred) if with_pred else None,
                                     "case_ids": [r["case_id"] for r in hid]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="v1,B3,B1,B2")
    ap.add_argument("--out", default="results/run1")
    ap.add_argument("--cases", default="")
    a = ap.parse_args()
    metas = load_cases()
    cids = a.cases.split(",") if a.cases else list(metas)
    rows, rows_va = [], []
    for m in a.methods.split(","):
        for c in cids:
            r = score_case(m, c, metas[c])
            if r:
                rows.append(r)
            if m == "v1":
                rv = score_case(m, c, metas[c], value_aware=True)
                if rv:
                    rv["method"] = "v1_value_aware"
                    rows_va.append(rv)
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    with (out / "per_case.jsonl").open("w", encoding="utf-8") as f:
        for r in rows + rows_va:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = aggregate(rows + rows_va)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for m, s in summary["overall"].items():
        print(f"{m:16s} n={s['n']:2d} itemF1={s['item_f1']:.3f} cellF1={s['cell_f1']:.3f} "
              f"trap={s['trap_rate_cell']:.3f} extra={s['extra_cells']:.1f} empty={s['empty_pred']} "
              f"trunc={s['truncated']} parse_fail={s['parse_fail']}")


if __name__ == "__main__":
    main()
