"""기준 출력 회귀 테스트용 녹화 응답 만들기(한 번만 실행).

cascade_dev_v1 full 실행(Qwen3-32B-FP8, pilot_data/results/cascade)의 케이스 로그와 예측에서 LLM 응답을 되살린다.
- locator 응답: 로그의 locator.judgments(id, relevant, reason)
- editor 응답: 예측 edits 가운데 source=llm인 편집(site, new_text). 고리를 한 바퀴만 돈 케이스만 고른다.
기대 출력은 같은 실행의 예측 edits다. 정답(gold)은 읽지 않는다.

사용: python tests/cascade/data/regression/make_fixture.py /home/jkpark/workspace/pilot_data
"""
import json
import sys
from pathlib import Path

CASES = ("T1_05", "T2_06", "T3_05")
HERE = Path(__file__).resolve().parent


def main(pilot_data):
    root = Path(pilot_data) / "results" / "cascade"
    for cid in CASES:
        pred = json.loads((root / "predictions" / "cascade_full" / f"{cid}.json").read_text(encoding="utf-8"))
        log = json.loads((root / "logs" / "cascade_full" / f"{cid}.json").read_text(encoding="utf-8"))
        assert pred["rounds"] == 1 and pred["stop_reason"] == "no_issues", cid
        names = [c["name"] for c in log["calls"]]
        responses = []
        for name in names:
            if name == "locator":
                responses.append({"judgments": [{"id": j["id"], "relevant": j["relevant"], "reason": j["reason"]}
                                                for j in log["locator"]["judgments"]]})
            elif name == "editor":
                responses.append({"edits": [{"site": e["site"], "new_text": e["new_text"]}
                                            for e in pred["edits"] if e["source"] == "llm"]})
            else:
                raise ValueError(f"{cid}: unexpected call {name}")
        out = {"case_id": cid, "source": "cascade_dev_v1 results/cascade/cascade_full", "calls": names,
               "config_hash": pred["config_hash"], "responses": responses, "expected_edits": pred["edits"]}
        (HERE / f"{cid}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(cid, names, len(pred["edits"]))


if __name__ == "__main__":
    main(sys.argv[1])
