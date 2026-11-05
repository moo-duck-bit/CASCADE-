# v03

본실험용 v0.3 데이터. 규격은 spec.md에 먼저 고정했다. P1(수면코치)의 사실 표와 문서 3종, 시트, 케이스 180개(cases/)까지 만들었다. 케이스 범위와 시트 수정은 spec.md 변경 기록(2026-11-04)에 있다.
v0.2 데이터(pilot_data/v02)는 건드리지 않았다. 템플릿(pilot_data/templates)과 렌더·검사 코드(pilot_data/lib)는 v0.2와 같이 쓴다.

## 폴더

- `spec.md` 규격(제품 규모, 경로·유형 분리, 보안 약화 금지 목록, 문서 수 균형, 정답 규칙, 생성 모델)
- `build/` 생성 코드. 제품은 환경변수 PILOT_PRODUCT(기본 P1)로 고른다.
- `products/P1/profile.json` 요구사항 범위, 사이버보안 속성 7개와 규칙 10개, 목표 수치
- `products/P1/product/` facts.json, links.json, content.json
- `products/P1/docs/` MDSR.docx, MDDR.docx, XXCS.docx, sites.json, site_index.md
- `products/P1/sheets/` fact_list.csv, cyber_attrs.csv, req_list.csv, case_spec.csv(제품 3종 60개용 빈 시트), case_spec_180_v0.csv(사람이 고른 180개 원본), case_spec_180.csv(수정본, 케이스 생성 입력)
- `cases/` 케이스 180개. `<id>/input`(문서 사본, site_index.md, request.txt, request_nl.txt), `<id>/gold`(impact.json, must_not_touch.json), `<id>/meta.json`, `index.json`
- `runs/P1/t3_add_drafts.json` T3 추가 새 Req 문장 42개, `runs/P1/check_cases.json`·`distribution.md` 케이스 검사 결과와 분포, `runs/P1/case_review.csv` 사람 검수표
- `products/P2`, `products/P3` 사양 카드(profile.md)만 있음
- `runs/gen_log.jsonl` LLM 호출 기록, `runs/P1/` 단계별 채택 출력과 보정 기록, 문서 검사 결과(check_docs.json)

## 다시 만들기

서버는 pilot_data/server/serve_qwen.sh로 띄운다(빈 GPU 확인 후). 아래 명령은 pilot_data/에서 실행한다.

```bash
GPU=0 server/serve_qwen.sh
```

```bash
PY=python
$PY v03/build/make_facts.py && $PY v03/build/check_facts.py
$PY v03/build/make_content.py && $PY v03/build/render_docs.py && $PY v03/build/check_docs.py
$PY v03/build/make_sheets.py
```

채택된 출력은 runs/P1/facts_rounds/*_accepted.json과 runs/P1/content_cache/에 남아 있어서, 다시 실행하면 LLM을 부르지 않고 같은 결과를 만든다.

케이스는 서버 없이 만든다. 시트 수정, 케이스 생성, 검사 순서다.

```bash
PY=python
$PY v03/build/revise_case_spec.py && $PY v03/build/make_cases.py && $PY v03/build/check_cases.py
```

## 생성 모델

Qwen3-32B-FP8(파일럿과 같은 모델, revision aa55da1)로 만들었다. 지정 모델 EXAONE-4.0.1-32B는 공개본이 BF16 64GB뿐이고 서버의 쓸 수 있는 디스크 여유가 66GB라 받지 못했다. 평가 모델과 같은 계열이라는 점은 spec.md 10절에 경고로 적었다.

## 코드가 모델 출력을 고친 곳

모델이 검사를 반복해서 통과하지 못한 부분은 규칙을 코드로 옮겼다. 모두 결정적이고 기록이 남는다.

- 사실: v0.2 사실 25개는 그대로 이어받고 모델은 새 사실 12개만 만들었다. 새 사실이 기존 사실과 값·단위가 같으면 같은 값 묶음으로 자동으로 넣었다(값이 같은데 묶음 밖이면 v0.2 규칙상 모순이라서). 단위가 다른 충돌은 모델에게 값을 바꾸게 했다.
- 사실 보정(runs/P1/facts_rounds/facts_postfix.json): 새 보안 사실 중 보관 기간 2개의 stronger_when을 lower에서 higher로 바꿨다(보관 기간은 길수록 강함, v0.2 감사로그 보관 기간과 같은 방향). 의료진 계정 권한 종류의 표기를 "4가지 / 4 가지 / 네 가지"로 맞췄다.
- 배치(runs/P1/facts_rounds/placement_trim.json): 모델이 타임스탬프 항목(UC-06)에 기능 사실 18개를 몰아 넣었다. 한 항목에 사실 4개까지만 남겼다(보안 사실, XXCS 항목 2개 이상인 사실 순서). 18건을 뺐다(UC-06 15, UC-04 2, SI-05 1).
- 요구사항: v0.2 Req 21개 제목은 코드가 고정했다. 보안 사실을 쓰는 Req는 security=true로 뒀다(spec.md 7절 정의). 그래서 처방 변경 이력(Req. 18), 데이터 보존·파기(Req. 107), 의료진 계정·권한 관리(Req. 108)는 보안 요구사항(삭제 금지)이다.
- 연결: 항목에 놓인 사실마다 그 사실을 쓰는 Req가 relevant로 연결되도록 relevant Req는 코드가 greedy로 정했다. 모델은 추가 relevant(코드가 정한 Req가 1개 이하일 때 1개)와 과연결 Req를 골랐다. 삭제 케이스에 trace 정답이 생기게 비보안 Req 13개를 항목마다 돌아가며 과연결 1개로 지정했다. relevant=false 비율은 정확히 0.5다(미보정).
- 본문: 사실 4개가 놓인 XXCS 항목과 3.4 보안 개요(토큰 16개)는 문장 수 상한을 넘겨 실패했다. 상한을 늘리고(XXCS 4문장, 3.4 8문단) 한 문장에 토큰을 묶으라는 안내를 넣어 그 3개 항목만 다시 만들었다(prompt_version v1.1·v1.2).

## 검사 결과

- 사실 검사(build/check_facts.py): facts·placement·reqs·links·rules 모두 통과. 사실 37개(보안 17), 같은 값 묶음 6개 24쌍, XXCS 2개 이상 행에 놓인 사실 17개, 2개 이상 Req가 쓰는 사실 12개, 비보안 Req 13개(전부 연결 있음).
- 문서 검사(build/check_docs.py): 남은 중괄호 0, 직접 기재 값 0, 배치 일치 153/153, 3종 재열기 정상, 모든 Req 기준 2문단.
- 사람이 볼 것: 표기 경고(숫자가 다른 표기, 예 "6개월"=180일, "1개월 반"=45일)와 문장 어색함(예: MDDR Req. 15 설계#p2가 이완 훈련 시간을 "종료 10분 전" 의미로 씀). review는 sheets와 site_index.md로 한다.
