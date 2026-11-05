"""CASCADE dev2(pilot_base cascade 브랜치, cascade_dev_v1 이후)를 케이스 폴더에 돌려 예측·로그를 저장한다.

cascade_adapter.py(dev1)와 같은 방식이고, 설정 덮어쓰기·서버 주소·케이스 폴더·출력 위치를 받는다.
케이스의 input/ 경로만 넘기며 정답은 CASCADE 쪽 가드가 막는다. 결과가 있는 케이스는 건너뛴다(이어 돌리기).

사용:
  python experiments/cascade2_adapter.py --preset full --method c2_full
  python experiments/cascade2_adapter.py --preset full --set edit_mode=free_json --method c2_free_json
  python experiments/cascade2_adapter.py --preset full --set loop_k=0 --method c2_k0 --base-url http://127.0.0.1:8102/v1
  python experiments/cascade2_adapter.py --preset full --cases-dir experiments/work/stress/cases --method c2_stress_full
출력: results/cascade2/predictions/<method>/<case>.json, results/cascade2/logs/<method>/<case>.json
"""
import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# CASCADE_SRC: 평가 중 코드 편집의 영향을 막으려고 특정 커밋의 src 사본을 쓸 때(git archive로 만든 폴더)
sys.path.insert(0, os.environ.get("CASCADE_SRC") or str(ROOT / "src" if (ROOT / "src").is_dir() else ROOT.parent / "pilot_base" / "src"))

from document_ai.cascade import config as cfg  # noqa: E402
from document_ai.cascade.run import run_case  # noqa: E402

WORK = ROOT / "experiments" / "work" / "cascade2"


def parse_set(items):
    out = {}
    for it in items:
        k, _, v = it.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def case_ids(cases_dir, prefixes):
    return sorted(p.name for p in Path(cases_dir).iterdir()
                  if p.is_dir() and (p / "input").exists() and p.name.split("_")[0] in prefixes)


def run_one(cid, cases_dir, config, method, out, keep_work):
    pred_path = out / "predictions" / method / f"{cid}.json"
    if pred_path.exists():
        return cid, None
    work = WORK / method / cid
    shutil.rmtree(work, ignore_errors=True)
    pred, log = run_case(Path(cases_dir) / cid / "input", config, work_dir=work, case_id=cid, method=method)
    log_path = out / "logs" / method / f"{cid}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    pred["log_path"] = str(log_path.relative_to(ROOT))
    pred_path.parent.mkdir(parents=True, exist_ok=True)
    pred_path.write_text(json.dumps(pred, ensure_ascii=False, indent=1), encoding="utf-8")
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return cid, pred


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="full")
    ap.add_argument("--set", nargs="*", default=[], help="설정 덮어쓰기 key=value(값은 JSON으로 읽음)")
    ap.add_argument("--method", required=True)
    ap.add_argument("--cases", default="", help="쉼표로 구분한 케이스 id")
    ap.add_argument("--types", default="T1,T2,T3", help="케이스 id 앞부분(T1,T2,T3)")
    ap.add_argument("--cases-dir", default=str(ROOT / "v02" / "cases"))
    ap.add_argument("--out", default=str(ROOT / "results" / "cascade2"))
    ap.add_argument("--base-url", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--keep-work", action="store_true")
    a = ap.parse_args()
    over = parse_set(a.set)
    if a.base_url:
        over["base_url"] = a.base_url
    config = cfg.preset(a.preset, **over)
    out = Path(a.out).resolve()
    cids = a.cases.split(",") if a.cases else case_ids(a.cases_dir, a.types.split(","))
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        for cid, pred in ex.map(lambda c: run_one(c, a.cases_dir, config, a.method, out, a.keep_work), cids):
            if pred is None:
                print(f"{cid} skip (exists)", flush=True)
                continue
            print(f"{cid} edits={pred['n_pred']} calls={pred['n_calls']} tok={pred['input_tokens']}+"
                  f"{pred['output_tokens']} {pred['seconds']}s stop={pred['stop_reason']} "
                  f"exec_err={pred.get('exec_errors')} err={pred['error']}", flush=True)
    print(f"{a.method}: {len(cids)} cases, {time.time() - t0:.0f}s, config {config.hash()}", flush=True)


if __name__ == "__main__":
    main()
