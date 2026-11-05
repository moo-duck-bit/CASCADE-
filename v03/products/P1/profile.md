# P1 수면코치 (사양 카드)

- 제품: 성인 불면증 환자용 불면증 인지행동치료(CBT-I) 디지털 치료기기 소프트웨어. 가상 제품.
- 구성: 환자용 모바일 앱, 서버, 의료진용 대시보드.
- 인증: 아이디·비밀번호 로그인.
- 저장 정보: 수면일기, 치료 진행 기록. 개인식별정보(이름·연락처)는 서버에 저장하지 않는다(stores_pii=false).
- 요구사항: 앱 Req. 1~18, 서버 Req. 100~108.
- 사이버보안 속성: password_auth=true, stores_pii=false, session_termination=true, samd=true, mobile_code=false, e_signature=false, remote_update=false. 규칙은 profile.json의 rules 10개.
- v0.2 제품(pilot_data/product)의 사실 25개를 그대로 이어받고 사실·요구사항·속성을 늘렸다.
- 생성 모델: Qwen3-32B-FP8(동일 계열 경고, v03/spec.md 10절).
