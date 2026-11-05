# P2 아동 ADHD 주의력 게임 (사양 카드, 아직 생성하지 않음)

- 제품: 만 6~12세 ADHD 아동의 주의력 향상을 위한 게임형 디지털 치료기기 소프트웨어. 가상 제품.
- 구성: 아동용 모바일 앱(Android, iOS), 보호자용 앱 화면, 서버, 의료진용 대시보드.
- 인증: 보호자 이메일 + 비밀번호로 가입·로그인. 아동은 보호자 계정 아래 프로필로 들어간다.
- 저장 정보: 아동의 연령·성별, 게임 수행 기록(반응 시간, 정답률), 치료 회기 기록. 개인식별정보 저장 → stores_pii=true로 시작.
- 플랫폼: Android와 iOS 두 가지. 앱 마켓 배포.
- 사이버보안 속성 후보: password_auth=true, stores_pii=true, session_termination=true, samd=true, mobile_code, e_signature, remote_update, multi_platform(Android+iOS). T2로 쓸 수 있는 방향은 생성 전에 spec.md 7절 기준으로 다시 정한다.
- 사실 예: 1회 게임 시간, 하루 최대 플레이 시간, 회기 수, 보호자 알림 시각, 난이도 단계 수, 비밀번호 규칙, 세션 종료 시간, 감사로그 보관 기간 등.
- 요구사항 규모: P1과 같게(앱 Req. 1~18, 서버 Req. 100~108 정도).
- 생성 모델: 가능하면 Qwen 계열이 아닌 모델(spec.md 10절의 동일 계열 경고 때문).
