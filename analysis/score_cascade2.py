"""CASCADE dev2 예측(results/cascade2/predictions/<method>/)을 기존 채점기(scoring/score.py)로 채점한다. score.py는 고치지 않는다.

score.load_pred를 바꿔 끼워 results/cascade2 → results/cascade → run1·run2 원래 위치 순서로 찾는다.
--cases-dir를 주면 그 폴더의 케이스 id만 채점한다(변형 세트도 정답은 v0.2 같은 id의 정답: 위치 불변).
사용: python analysis/score_cascade2.py [--methods c2_full,B2V] [--out results/cascade2]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scoring"))
import score  # noqa: E402

PRED_DIRS = [ROOT / "results" / "cascade2" / "predictions", ROOT / "results" / "cascade" / "predictions"]
BASELINES = "v1,B1,B2,B3,B2V,B2SC"
_orig = score.load_pred


def load_pred(method, cid):
    for d in PRED_DIRS:
        p = d / method / f"{cid}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return _orig(method, cid)


score.load_pred = load_pred


def methods_available():
    return sorted(p.name for p in PRED_DIRS[0].iterdir() if p.is_dir()) if PRED_DIRS[0].exists() else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="")
    ap.add_argument("--out", default="results/cascade2")
    a = ap.parse_args()
    methods = a.methods.split(",") if a.methods else methods_available() + ["cascade_full"] + BASELINES.split(",")
    metas = score.load_cases()
    rows = [r for m in methods for c in metas if (r := score.score_case(m, c, metas[c]))]
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    with (out / "per_case.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = score.aggregate(rows)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for m, s in summary["overall"].items():
        print(f"{m:22s} n={s['n']:2d} itemF1={s['item_f1']:.3f} cellF1={s['cell_f1']:.3f} "
              f"trap={s['trap_rate_cell']:.3f} tok={s['tokens_in']:.0f}+{s['tokens_out']:.0f} sec={s['seconds']:.1f}")


if __name__ == "__main__":
    main()
