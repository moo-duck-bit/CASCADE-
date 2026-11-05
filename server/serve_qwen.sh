#!/usr/bin/env bash
# Qwen3-32B 서빙 (vLLM, OpenAI 호환). tmux 세션 pilot-qwen32b 에서 실행하고 server/vllm.log 에 로그를 남긴다.
#
#   ./serve_qwen.sh            # 시작 (기본: GPU0 1장, FP8)
#   ./serve_qwen.sh stop       # 종료
#   GPU=1 ./serve_qwen.sh      # 다른 빈 GPU 지정
#   MODE=bf16 GPU=1,2 ./serve_qwen.sh   # 빈 GPU 2장일 때 BF16 TP2
#   YARN=1 ./serve_qwen.sh     # 긴 입력용 YaRN(factor 4, max-model-len 65536)
#   MODE=bf16_14b YARN=1 GPU=0 ./serve_qwen.sh   # Qwen3-14B BF16 1장(served name qwen3-14b)
set -euo pipefail
cd "$(dirname "$0")"

SESSION=${SESSION:-pilot-qwen32b}
ENV_BIN=${VLLM_BIN:-$(dirname "$(command -v vllm)")}
PORT=${PORT:-8100}
GPU=${GPU:-0}
MODE=${MODE:-fp8}
YARN=${YARN:-0}
SEED=${SEED:-0}
GPU_UTIL=${GPU_UTIL:-0.95}
LOG=${LOG:-"$PWD/vllm.log"}

if [[ "${1:-}" == "stop" ]]; then
  tmux kill-session -t "$SESSION" 2>/dev/null && echo "stopped $SESSION" || echo "no session $SESSION"
  exit 0
fi

SERVED=qwen3-32b
if [[ "$MODE" == "bf16_14b" ]]; then
  # 두 번째 모델: Qwen3-14B BF16 1장, 나머지 설정(YaRN, prefix caching, chunked prefill, kv fp8)은 32B와 같다
  MODEL=Qwen/Qwen3-14B
  REVISION=40c069824f4251a91eefaf281ebe4c544efd3e18
  SERVED=qwen3-14b
  EXTRA=(--dtype bfloat16 --kv-cache-dtype fp8)
elif [[ "$MODE" == "bf16" ]]; then
  MODEL=Qwen/Qwen3-32B
  REVISION=9216db5781bf21249d130ec9da846c4624c16137
  EXTRA=(--tensor-parallel-size 2 --dtype bfloat16)
else
  MODEL=Qwen/Qwen3-32B-FP8
  REVISION=aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df
  EXTRA=(--kv-cache-dtype fp8)
fi

if [[ "$YARN" == "1" ]]; then
  MAXLEN=65536
  EXTRA+=(--rope-scaling '{"rope_type":"yarn","factor":4.0,"original_max_position_embeddings":32768}')
else
  MAXLEN=32768
fi

CMD=(env CUDA_VISIBLE_DEVICES="$GPU" "$ENV_BIN/vllm" serve "$MODEL"
  --revision "$REVISION"
  --served-model-name "$SERVED"
  --host 127.0.0.1 --port "$PORT"
  --max-model-len "$MAXLEN"
  --gpu-memory-utilization "$GPU_UTIL"
  --enable-prefix-caching
  --enable-chunked-prefill --max-num-batched-tokens 4096
  --seed "$SEED"
  --generation-config vllm
  "${EXTRA[@]}")

printf -v CMD_STR '%q ' "${CMD[@]}"
echo "$(date -Is) start: $CMD_STR" >> "$LOG"
tmux new-session -d -s "$SESSION" "bash -lc '$CMD_STR >> $LOG 2>&1'"
echo "started tmux session $SESSION (GPU=$GPU MODE=$MODE YARN=$YARN port=$PORT), log: $LOG"
