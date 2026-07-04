# next — 지금 할 일

상태: **M5 완료(편집 UI 연동) · M3 오디오 먹싱만 미착수** · 전체 순서는 [ROADMAP.md](ROADMAP.md) 참조

이 파일은 살아있는 체크리스트다. 마일스톤이 끝나면 완료 표시하고, 다음 마일스톤의 세부
작업으로 내용을 갈아치운다 (지난 마일스톤 기록은 ROADMAP.md 표에만 남긴다 — 여기서 중복 안 함).

## M5 — 편집 UI 연동 (완료, 2026-07-04)

- [x] `channels.py` 신규: 채널 데이터 모델(`Channel`: x,y,w,h,tracking,target_id,zoom,
      smoothing) — `modes.py`의 3-모드는 그대로 두고(`reframe.py` CLI가 계속 씀), 서버
      전용으로 자유 편집 가능한 채널 리스트 도입. 채널마다 독립 `Smoother`+`Presence`.
      줌 프리셋 배율은 `modes.py`의 `render_single` 실측값(full=1.3/waist=0.7/face=0.35)
      재사용 — `mockup/index.html`의 눈대중 값(0.32 등) 대신.
- [x] `server.py` 확장: 고정 `--mode` 분기 대신 `channels: list[Channel]`로 매 프레임
      렌더 + `CommandQueue`로 CRUD(추가/수정/삭제/프리셋) 처리. 채널별 RTSP/NDI publisher를
      동적으로 열고 닫음(`ChannelOutputs`). 프리뷰를 `composite()` 2×2 그리드에서 **원본
      다운스케일 프레임**으로 전환(크롭 드래그는 원본 위에서 해야 함 — UI-PLAN.md §2 원안).
      오버레이에 `channels`(정규화 rect+상태) + `frame_w`/`frame_h` 추가.
      신규 엔드포인트: `GET/POST /api/channels`, `PATCH`/`DELETE /api/channels/{id}`,
      `POST /api/preset/{multi|quad|single}`.
- [x] `console/index.html` 대폭 확장: `mockup/index.html`의 UI/인터랙션(드래그 이동·
      리사이즈, 인물 클릭 바인딩, 트래킹 토글, 줌 프리셋, 스무딩 슬라이더, 프리셋 버튼,
      채널 추가/삭제)을 실제 API/WS에 배선. 좌표는 전부 정규화(0-1)로 다뤄 해상도 전환에도
      안전(M4의 입력 전환과 조합 가능).
- [x] 버그 수정: `detect_people()`이 반환하는 `numpy.float32`를 그대로 WS JSON에 실으면
      `TypeError: Object of type float32 is not JSON serializable` — 사람이 실제로 감지될
      때만 터지는 문제라 M4 테스트(합성 영상, 사람 없음)에선 못 잡았음. `boxes` 구성 시
      `int()`/`float()`로 명시 캐스팅해 해결.

### 검증 완료

- [x] `reframe-server --self-test`: `channels.py` 핵심 로직(고정 크롭, 대기 placeholder,
      줌 프리셋 계산, 상태 전이) + 채널 CRUD 엔드포인트 왕복 통과
- [x] 실카메라(J0Sunvail Camera)로 실동작 확인: `/api/sources` 썸네일로 카메라 식별 →
      전환 → 인물 감지 → `PATCH target_id`로 바인딩 → 크롭이 실제로 얼굴/상반신을 따라가는
      것을 해당 채널의 RTSP 출력에서 프레임 캡처로 확인
- [x] 채널 삭제 시 해당 RTSP 스트림이 즉시 404로 사라지는 것 확인(ffprobe)
- [x] 프리셋 전환(MULTI→QUAD→SINGLE) 시 채널 4개가 올바른 좌표/트래킹 상태로 재구성되는
      것 확인, SINGLE의 full/waist/face 줌 3단계가 시각적으로 뚜렷하게 다름을 프레임
      캡처로 확인
- [ ] MULTI/QUAD의 "full" 줌이 데스크 세팅에서 풀프레임으로 클램프되는 현상 재확인(M3와
      동일한 원인 — 카메라-피사체 거리 부족, 코드 문제 아님. `MIN_CROP_FRACTION` 로직이
      새 채널 모델에도 동일하게 올바르게 적용되고 있다는 뜻이라 오히려 정상 신호)
- [ ] `console/index.html`의 드래그/리사이즈 인터랙션 자체는 이번 세션에 브라우저로
      직접 조작해보지 않음(API/백엔드까지만 자동 검증) — 실제 마우스 조작 확인 필요

## M4 — 4채널 확장 + 컨트롤 서버 (완료, 2026-07-04)

- [x] `sources.py`: OpenCV 인덱스 기반 장치 probe + 썸네일 + 해상도 probe. ffmpeg 장치
      이름을 안 믿는 이유는 UI-PLAN.md §2a에 실측 근거와 함께 기록.
- [x] `state.py`: `PipelineState`(락으로 보호되는 최신 프레임/오버레이/소스 인덱스),
      `CommandQueue`(입력 전환 커맨드 전달).
- [x] `server.py`/`reframe-server`: FastAPI 단일 프로세스(캡처+추론 루프는 백그라운드
      스레드) — `/api/state`, `/api/sources`, `/api/sources/{id}/thumbnail.jpg`,
      `/api/sources/{id}/resolutions`, `POST /api/input`, `/api/preview.mjpg`(MJPEG),
      `WS /ws`(오버레이 ~10Hz). `--self-test`로 TestClient 기반 회귀 테스트.
- [x] 4채널 송출: `--rtsp-out-base`/`--ndi-out-base`로 tiles[0..3] 각각 독립 publisher —
      실카메라(Cam Link 4K)로 RTSP 4채널(ffprobe) + NDI 4채널(Finder 디스커버리) 모두 확인.
- [x] `console/index.html`: 읽기 전용 프리뷰(MJPEG) + 캔버스 오버레이(WS) + 썸네일 기반
      소스 선택 + 해상도 드롭다운 + 적용.
- [x] 문서 정정: UI-PLAN.md §2a(썸네일 기반 선택으로), INFRA-PLAN.md §2(프로세스 분리
      M6으로 유예 각주).

### 검증 완료 / 남은 것

- [x] 백엔드 API 전부 curl/websockets 스크립트로 실동작 확인, MJPEG 프레임 시각 확인
- [x] 4채널 RTSP·NDI 각각 독립 송출 확인
- [ ] `console/index.html`을 실제 브라우저로 열어 캔버스 오버레이 렌더링 육안 확인(이번
      세션은 API/백엔드까지만 자동 검증 — 프런트엔드 JS 자체는 브라우저에서 직접 확인 필요)

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
인덱스가 다르므로 프레임 내용으로 직접 대조해 확인(`/tmp/cam_probe2_0.jpg` 등) — 이 경험이
M4의 `sources.py` 썸네일 기반 선택 설계로 이어짐.

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

M3(오디오 먹싱 제외)·M4 핵심 검증은 끝났다. 남은 건:

1. `console/index.html` 실제 브라우저 육안 확인
2. 오디오 먹싱 — 캡처카드나 마이크 입력으로 A/V 동기 검증
3. M5(편집 UI 연동) — `mockup/index.html`의 크롭 이동/리사이즈/트래킹 바인딩을 M4의
   `/api/*` 백엔드에 실배선 (지금은 `console/index.html`이 읽기 전용이라 별도 페이지)
