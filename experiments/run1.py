"""run1 실행: 현재 파이프라인(v1)과 베이스라인 B3·B1·B2. 결과 파일이 있는 케이스는 건너뛴다.

인자가 없으면 v1 → B3 → B1 → B2 순서로 돌린다. B1 처음 5케이스로 잰 예상 총 시간이 8시간을 넘으면
B2를 T1·T2로 줄이고 results/run1/eta.log, b2_scope.json 에 남긴다. v1은 conda env pilot_base 의 python이 필요하다.
사용: python experiments/run1.py [--methods v1,B3,B1,B2] [--cases T1_01,T2_01]
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor

from baselines import OUT, append_log, doc_order, load_cases, run_one, run_timed

LOG = OUT.parent / "eta.log"
LIMIT_H = 8.0


def log(msg):
    append_log(LOG, msg)


def run_v1(cids, metas):
    import current_pipeline  # pilot_base 패키지를 import 하므로 v1을 돌릴 때만 불러온다
    for c in cids:
        if not (OUT / "v1" / f"{c}.json").exists():
            current_pipeline.run_case(c, metas[c])


def run_method(method, cids, metas, eta_hook=None):
    run_timed(method, cids, metas, lambda c, meta: run_one(method, c, meta), OUT, log, eta_hook)


def run_all(metas):
    t_start = time.time()
    cids = list(metas)
    log("run1 start (order v1 → B3 → B1 → B2, workers=2)")
    t = time.time()
    run_v1(cids, metas)
    log(f"v1: finished in {time.time() - t:.1f}s")
    run_method("B3", cids, metas)
    scope = {"B2_types": ["T1", "T2", "T3"], "reduced": False}
    b2_cases = len([c for c in cids if not (OUT / "B2" / f"{c}.json").exists()])

    def b1_hook(per_case):
        # 지금까지 + B1 나머지 + B2 전체. B2 케이스당 시간은 B1 값으로 잡는다.
        b1_left = len([c for c in cids if not (OUT / "B1" / f"{c}.json").exists()])
        elapsed_h = (time.time() - t_start) / 3600
        total_h = elapsed_h + per_case * (b1_left + b2_cases) / 3600
        log(f"ETA: elapsed {elapsed_h:.2f}h + B1 left {b1_left} + B2 {b2_cases} cases × {per_case:.0f}s "
            f"≈ total {total_h:.2f}h (limit {LIMIT_H}h)")
        if total_h > LIMIT_H:
            scope.update(B2_types=["T1", "T2"], reduced=True, reason=f"예상 총 {total_h:.2f}h > {LIMIT_H}h")
            log("ETA over limit → B2 reduced to T1,T2")

    run_method("B1", cids, metas, eta_hook=b1_hook)
    (OUT.parent / "b2_scope.json").write_text(json.dumps(scope, ensure_ascii=False, indent=1), encoding="utf-8")
    run_method("B2", [c for c in cids if metas[c]["case_type"] in scope["B2_types"]], metas)
    log(f"run1 done in {(time.time() - t_start) / 3600:.2f}h")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="")
    ap.add_argument("--cases", default="")
    a = ap.parse_args()
    metas = load_cases()
    if not a.methods and not a.cases:
        run_all(metas)
        return
    cids = a.cases.split(",") if a.cases else list(metas)
    for method in (a.methods or "v1,B3,B1,B2").split(","):
        if method == "v1":
            run_v1(cids, metas)
            continue
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda c: run_one(method, c, metas[c]), doc_order(cids, metas)))


if __name__ == "__main__":
    main()
