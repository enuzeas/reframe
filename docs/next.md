# next — 지금 할 일

상태: **M3 부분 완료 · 4K 카메라 대기로 일시정지** · 전체 순서는 [ROADMAP.md](ROADMAP.md) 참조

이 파일은 살아있는 체크리스트다. 마일스톤이 끝나면 완료 표시하고, 다음 마일스톤의 세부
작업으로 내용을 갈아치운다 (지난 마일스톤 기록은 ROADMAP.md 표에만 남긴다 — 여기서 중복 안 함).

## M3 — 송출 PoC (부분 완료, 2026-07-04)

### 완료

- [x] `output.py` 신규: `RTSPPublisher` — 파이프라인 프레임을 h264_videotoolbox로 인코딩해
      RTSP publish. `reframe.py --rtsp-out <url>`, `--no-preview` 옵션 추가
- [x] mediamtx 실치·기동 확인 (`brew bundle`) — 최소 설정 `paths: all_others:` 필요했음
      (빈 설정으로 띄우면 임의 경로 publish가 400/404로 막힘)
- [x] RTSP 왕복 실동작 확인: reframe 파이프라인(실제 웹캠) → ffmpeg → mediamtx → **OBS Media
      Source에서 실제 수신** 확인
- [x] 지연 튜닝: `-rtsp_transport udp`(TCP보다 낮음, 로컬망이라 손실 걱정 없음) · `-bf 0`(B프레임 제거)
      · `-g <fps>`(GOP 단축) — 사용자 체감 "지연 많이 좋아짐"

### 실사용 중 발견·수정한 버그 2건

1. **`tracking.py` 줌 클램프**: 크롭 높이 하한이 절대값 `HD_H`(1080px)였음 — 4K(2160px)를
   전제로 한 값이라 720p 웹캠 등 더 작은 소스에서는 크롭이 항상 프레임 전체로 clamp됨(패닝
   여백 없음). `MIN_CROP_FRACTION`(프레임 높이의 0.5배)으로 교체해 소스 해상도에 비례하도록 수정.
2. **`smoothing.py` One Euro `min_cutoff`**: 1.0은 평범한 속도의 움직임을 노이즈로 취급해
   억제하고, 빠르고 큰 움직임(프레임 이탈)에서만 반응했음. 3.0으로 올려 실측(`smoothed_cx`가
   `raw_cx`를 거의 그대로 추종)으로 확인 완료.

### NDI 경로 PoC (완료, 2026-07-04)

- [x] Syphon 대신 NDI로 방향 전환 — 이 머신엔 Syphon 플러그인이 없고 ffmpeg도 Syphon 출력을
      지원 안 함. 반대로 obs-ndi 플러그인과 NDI SDK(`libndi.dylib`, NDI Tools 설치분)는
      이미 있어서 마찰이 훨씬 적었음.
- [x] `output.py`에 `NDIPublisher` 추가 (`cyndilib` 사용, BGRX fourcc, `Fraction`으로 프레임레이트
      지정 필요 — 그냥 float 넘기면 `AttributeError`). `reframe.py --ndi-out <name>` 옵션 추가,
      RTSP와 동시 송출도 가능하도록 `publishers` 리스트로 일반화.
- [x] 실동작 확인: 합성 4K 루프 영상 → 파이프라인 → `NDIPublisher` → 별도 프로세스의
      `cyndilib.finder.Finder`가 mDNS로 `reframe-live-test` 소스를 실제로 발견 (네트워크
      레벨 왕복 확인 완료).
- [x] **OBS 육안 확인 완료(2026-07-04)**: OBS NDI Source에서 `reframe-out1` 선택 → 실제
      웹캠(FaceTime HD Camera, index 3) 프레임이 화면에 나오는 것 확인. RTSP와 마찬가지로
      NDI 경로도 끝까지(발행→디스커버리→OBS 렌더링) 검증 완료.
- [x] `pyproject.toml`에 `cyndilib` 추가, `Brewfile`에 NDI SDK가 brew로 설치 안 되는 이유와
      설치처(NDI Tools) 메모.

### 줌/패닝 시각 검증 (완료, 2026-07-04 — 실제 4K 카메라)

Elgato Cam Link 4K 확보 후 실측. 장치 인덱스는 이전과 마찬가지로 ffmpeg 이름과 OpenCV
인덱스가 다르므로 프레임 내용으로 직접 대조해 확인(`/tmp/cam_probe2_0.jpg` 등).

- **MULTI 모드(zoom=1.6, 풀바디 목표)**: 데스크 세팅이라 카메라-피사체 거리가 짧아
  `bbox_h`가 프레임의 71~76%를 차지 → `1.6배` 곱하면 프레임 높이를 넘어서 `clamp_window`가
  크롭을 프레임 전체로 깎아버림(패닝도 사라짐). **코드 버그 아님** — 렌즈는 이미 최대
  와이드(맞는 선택, 화각을 넓힐수록 같은 거리에서 피사체 비율이 작아짐)라 남은 변수는
  순수 물리적 거리뿐인데, 방 구조상 더 물러날 공간이 없어 막힘. 이 모드는 **카메라-피사체
  거리가 충분히 확보된 환경(스튜디오 등)에서 조건부로 재검증** 필요.
- **SINGLE 모드 얼굴 클로즈업(zoom=0.35× bbox 높이)**: 목표 크롭 높이가 훨씬 작아 프레임
  클램프에 안 걸림 → **실제로 크롭되고, 자세가 바뀌어도 얼굴이 크롭 중심에 유지되는 것
  확인 완료**(`/tmp/reframe_face_0.jpg`, `_40.jpg`, `_100.jpg` 비교). 감지·추적·스무딩·
  클램프 로직 전부 정상 동작 확인 — M3의 "줌/패닝 육안 검증" 블로커는 이걸로 해소.

### 남은 것

- [ ] 오디오 먹싱 A/V 동기 검증 — 캡처카드 오디오 입력 필요
- [ ] (필요시만) WebRTC/WHEP 경로 확인
- [ ] MULTI 모드 풀바디 줌 재검증 — 카메라-피사체 거리 확보되는 환경에서

## 완료되면 (지금 여기)

M3의 핵심 검증은 끝났다. 남은 건:

1. 오디오 먹싱 — 캡처카드나 마이크 입력으로 A/V 동기 검증
2. M4(컨트롤 서버) 착수 — UI-PLAN.md §2a(입력 소스 선택)부터
