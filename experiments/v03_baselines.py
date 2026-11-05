"""v0.3 평가용 베이스라인·채점 래퍼(기존 run1·run2·baselines·score 코드는 고치지 않는다).

케이스 폴더와 출력 위치만 바꿔 끼운다: score.CASES ← $V03_CASES, 예측은 results/v03/{run1,run2}/predictions.
사용: python experiments/v03_baselines.py baselines B1,B2,B3   |   b2v   |   score <methods> <out>
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scoring"))
sys.path.insert(0, str(ROOT / "experiments"))
V03 = Path(os.environ.get("V03_CASES", ROOT / "v03" / "cases"))
OUT = ROOT / "results" / "v03"

import score  # noqa: E402

score.CASES = V03
score.RUN1_PRED = OUT / "run1" / "predictions"
score.RUN2_PRED = OUT / "run2" / "predictions"


def patch_baselines():
    import baselines
    baselines.CASES = V03
    baselines.OUT = OUT / "run1" / "predictions"
    baselines.CALLS = OUT / "run1" / "calls_baselines.jsonl"
    return baselines


def main():
    cmd = sys.argv[1]
    if cmd == "baselines":
        b = patch_baselines()
        import run1
        run1.OUT = b.OUT
        metas = score.load_cases()
        for m in sys.argv[2].split(","):
            for c in b.doc_order(list(metas), metas):
                b.run_one(m, c, metas[c])
    elif cmd == "b2v":
        patch_baselines()
        import run2
        run2.RESULTS, run2.OUT = OUT / "run2", OUT / "run2" / "predictions"
        run2.RUN1_B2 = OUT / "run1" / "predictions" / "B2"
        run2.run_stage("b2v", score.load_cases(), None, run2.MODEL)
    elif cmd == "score":
        sys.argv = ["score_cascade2.py", "--methods", sys.argv[2], "--out", sys.argv[3]]
        sys.path.insert(0, str(ROOT / "analysis"))
        import score_cascade2
        score_cascade2.PRED_DIRS[:] = [OUT / "cascade" / "predictions"]
        score_cascade2.score.CASES = V03
        score_cascade2.main()


if __name__ == "__main__":
    main()
