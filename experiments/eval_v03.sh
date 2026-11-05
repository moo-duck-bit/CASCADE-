#!/usr/bin/env bash
# v0.3 평가(실행하지 않고 준비만 해 둔 스크립트). 고정 상태와 코퍼스 해시가 다르면 즉시 멈춘다.
#   V03_CASES=v03/cases GPU 서버(:8100 32B, :8102 32B, :8103 14B, :8101 BGE-M3) 준비 후 실행
#   bash experiments/eval_v03.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export V03_CASES=${V03_CASES:-$PWD/v03/cases}
PY=${PY:-python}
BASE=$PWD
export CASCADE_CORPUS_DIR=$PWD/corpus

# 1) 고정 상태 확인: 설정·프롬프트·규칙 표·검사 명세·도메인 팩·연산 스키마·검색 설정·코퍼스 해시
( cd "$BASE" && PYTHONPATH=src $PY -m document_ai.cascade.freeze --check ) || { echo "FROZEN check failed: stop"; exit 1; }
# 2) 코퍼스 해시가 고정값과 같은지(FROZEN.json에 적힌 값과 regs.sha256, 실제 파일)
want=$($PY -c "import json;print(json.load(open('$BASE/src/document_ai/cascade/FROZEN.json'))['corpus']['sha256'])")
have=$(sha256sum corpus/regs.jsonl | cut -d' ' -f1)
fixed=$(cut -d' ' -f1 corpus/regs.sha256)
[[ "$want" == "$have" && "$have" == "$fixed" ]] || { echo "corpus hash mismatch ($want / $have / $fixed): stop"; exit 1; }
[[ -d "$V03_CASES" ]] || { echo "no v0.3 cases at $V03_CASES: stop"; exit 1; }

A="experiments/cascade2_adapter.py --cases-dir $V03_CASES --out results/v03/cascade --workers 6"
U1=http://127.0.0.1:8100/v1; U2=http://127.0.0.1:8102/v1; U14=http://127.0.0.1:8103/v1
run() { echo "=== $1 $(date +%T)"; $PY $A --method "$@"; }

# 3) 베이스라인
$PY experiments/v03_baselines.py baselines B1,B2,B3
$PY experiments/v03_baselines.py b2v
run v3_ledger --preset ledger_like --base-url $U2
# 4) CASCADE full과 제거 실험
run v3_full --preset full --base-url $U1
run v3_no_verifier --preset no_verifier --base-url $U2
run v3_no_relevance --preset no_relevance --base-url $U1
run v3_no_value_index --preset no_value_index --base-url $U2
run v3_no_rules --preset no_rules --base-url $U1
run v3_fake --preset fake_feedback --base-url $U2
run v3_free_json --preset full --set edit_mode=free_json --base-url $U1
# 5) K 곡선
for k in 0 1 3; do run v3_k$k --preset full --set loop_k=$k --base-url $U2; done
# 6) 검색기 × k (T2·T3)
run v3_ret_off --preset full --set retrieval=false --types T2,T3 --base-url $U1
for r in bm25 dense hybrid; do for k in 3 10 30; do
  run v3_${r}_k$k --preset full --set retriever=$r retrieval_k=$k --types T2,T3 --base-url $U2
done; done
# 7) 범용성: graph_mode, request_mode, rules_source
run v3_generic --preset full --set graph_mode=generic --base-url $U1
run v3_generic_rules --preset full --set graph_mode=generic_rules --base-url $U2
run v3_natural --preset full --set request_mode=natural --base-url $U1
run v3_rules_extracted --preset full --set rules_source=extracted --types T2,T3 --base-url $U2
run v3_rules_retonly --preset full --set rules_source=retrieval_only --types T2,T3 --base-url $U1
# 8) 14B
run v3_full_14b --preset full --set model=qwen3-14b --base-url $U14
# 9) 상용 모델(키가 있을 때만, 케이스당 비용 상한은 token_budget으로)
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  run v3_full_claude --preset full --set backend=anthropic model=claude-sonnet-5-5 token_budget=40000
fi
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  run v3_full_gpt --preset full --set backend=openai model=gpt-5-mini token_budget=40000
fi
# 10) 채점과 표
M=$(ls results/v03/cascade/predictions | paste -sd, -)
$PY experiments/v03_baselines.py score "$M,B1,B2,B3,B2V" results/v03
echo "v0.3 done $(date +%T)"
