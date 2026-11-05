#!/usr/bin/env bash
# v0.3 CASCADE full, Qwen3-32B-FP8, T1 72 + T3 63(T3_64~72 제외). 서버 두 대에 반씩 나눠 돌렸다. 인자: a|b
# 코드: src/(FROZEN v3, code_sha256 9c527775…), 데이터: results/v03/cascade/cases_manifest.sha256
set -euo pipefail
cd "$(dirname "$0")/../../../.."
export CASCADE_SRC=$PWD/src CASCADE_CORPUS_DIR=$PWD/corpus
PY=${PY:-python}
IDS=$(tr -d '\n' < results/v03/ids_t1_t3.txt)
if [[ $1 == a ]]; then U=http://127.0.0.1:8100/v1; IDS=$(echo "$IDS" | tr ',' '\n' | awk 'NR%2==1' | paste -sd,)
else U=http://127.0.0.1:8102/v1; IDS=$(echo "$IDS" | tr ',' '\n' | awk 'NR%2==0' | paste -sd,); fi
$PY experiments/cascade2_adapter.py --preset full --method v3_full --cases-dir v03/cases --out results/v03/cascade \
  --base-url $U --workers 6 --cases "$IDS"
