# CASCADE

의료기기 소프트웨어 문서(MDSR, MDDR, XXCS) 변경 전파 평가용 데이터셋 v0.3과 CASCADE 구현, 베이스라인 실행 코드.

## 폴더

- `v03/cases/` 평가 케이스 180개(T1 72, T2 36, T3 72). 케이스마다 `input/`(문서 사본, site_index.md, request.txt, request_nl.txt), `gold/`(impact.json 정답 칸, must_not_touch.json 함정 칸), `meta.json`. 전체 목록은 `v03/cases/index.json`.
- `v03/spec.md` 데이터셋 규격과 변경 기록. `v03/README.md` 생성 과정.
- `v03/products/P1/sheets/case_spec_180.csv` 케이스 시트, `v03/runs/P1/case_review.csv` 검수표.
- `src/document_ai/cascade/` CASCADE. 고정 설정은 `FROZEN.json`(v3).
- `experiments/` CASCADE 어댑터(cascade2_adapter.py), 베이스라인(baselines.py, run1.py, run2.py, v03_baselines.py), 전체 실행 스크립트(eval_v03.sh).
- `scoring/score.py` 채점기. `corpus/` 공개 규정 코퍼스(첨부3 등 가이드라인 본문). `server/` vLLM·임베딩 서버 스크립트.

## 현재 상태

- T2 23건(T2_04, 05, 08~12, 14, 15, 18~20, 24, 25, 29, 32~36)과 T3_64, 68, 69는 적용 예외 규칙이 첨부3 원문과 달라 수정 중이다. 지금은 T1 72개와 T3 63개(T3_64~72 제외)만 쓴다. 목록은 `results/v03/ids_t1_t3.txt`.
- 기본 문서 XXCS의 UC-07, SI-08, SI-09, RA-05 적용 여부가 원문과 다르다(이번 버전은 그대로 둔다. 이 행을 정답으로 묻는 케이스는 없다).

## 환경

- Python 3.11, `pip install python-docx`
- 모델 서버: vLLM 0.8.5, transformers 4.51.3, Qwen/Qwen3-32B-FP8(revision aa55da1). `GPU=0 PORT=8100 server/serve_qwen.sh` (긴 입력은 `YARN=1`)
- CASCADE 검색용 임베딩 서버: FlagEmbedding, BAAI/bge-m3. `CUDA_VISIBLE_DEVICES=1 python server/embed_server.py serve --port 8101`

## 실행

CASCADE(T1·T3 135개, 서버 두 대):

```bash
results/v03/cascade/run_scripts/run_v3_full_t1t3.sh a   # Qwen :8100
results/v03/cascade/run_scripts/run_v3_full_t1t3.sh b   # Qwen :8102
```

베이스라인은 `python experiments/v03_baselines.py baselines B1,B2,B3`, `python experiments/v03_baselines.py b2v`(B2 뒤). 모델 서버는 :8100.

테스트: `CASCADE_CORPUS_DIR=$PWD/corpus PYTHONPATH=src python -m pytest tests/cascade -q`

## 결과(v0.3 T1·T3, CASCADE full)

Qwen3-32B-FP8, FROZEN v3. 예측은 `results/v03/cascade/predictions/v3_full/`, 요약은 `results/v03/cascade/v3_full_t1t3_summary.txt`.

- 전체 135개 칸 F1 0.929(P 0.924, R 0.960), 함정 침범 0.059
- T1 72개 0.896, T3 63개 0.967(삭제 1.000, 추가 0.936, 추가+값 변경 0.942)
- 문서 1개 0.952, 2개 0.924, 3개 0.910
- 경로별 재현율: origin 0.990, same_id 0.892, trace 0.992, same_value 0.841
- 케이스당 LLM 호출 2.3회, 입력 7,185토큰, 131초. 실행 오류 0
- T3 추가 정답에는 MDDR 새 Req 번호 칸이 들어 있는데 CASCADE는 이 칸을 편집으로 보고하지 않는다. 이 칸을 정답에서 빼면 T3 추가류 칸 F1은 0.938에서 0.954가 된다.
