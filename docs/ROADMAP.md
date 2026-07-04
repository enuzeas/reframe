# reframe 통합 로드맵

작성일: 2026-07-03 · 상태: 기획 · 원본: [PLAN.md](PLAN.md) · [UI-PLAN.md](UI-PLAN.md) · [INFRA-PLAN.md](INFRA-PLAN.md)

세 문서에 흩어진 Phase/구현 단계를 하나의 순서 있는 로드맵으로 합친 것. 각 항목의 상세
설계·근거는 원본 문서에 있고, 여기는 순서·의존성·완료 기준만 다룬다.

## 정정 사항

- UI-PLAN.md §6의 1번 항목("MediaMTX + ffmpeg 1채널 송출 PoC — **OBS 브라우저 소스**에서 지연
  실측")은 같은 문서 §4가 나중에 개정되기 전 문구다. §4 개정(Syphon/NDI/RTSP 우선, WebRTC는
  예외)에 따라 M3에서 실측 대상을 Syphon/RTSP 중심으로 바꿔 반영했다.
- M2 완료 기준에 적어둔 `reframe-pipeline --self-test`는 아직 존재하지 않는 프로세스 분리
  (encode/api가 별도 프로세스로 나뉘는 건 M3/M4 이후)를 전제한 문구였다. 지금은 파이프라인과
  GUI가 한 스크립트라 진입점 1개(`reframe`)면 충분 — 아래 표에 반영.

## 마일스톤

| # | 마일스톤 | 핵심 작업 | 완료 기준 | 의존성 | 출처 |
|---|---|---|---|---|---|
| M0 | 코어 파이프라인 프로토타입 | ✅ 완료 — 입력→YOLO+ByteTrack→3모드 크롭→2×2 프리뷰, self-test. 실제 웹캠(FaceTime HD Camera)으로 인물 추적까지 검증 | `python reframe.py --self-test` 통과 + 실제 인물 추적 확인 | — | PLAN.md Phase1 (첫 항목) |
| M1 | 파이프라인 안정화·실측 | ✅ 완료(2026-07-04) — One Euro Filter 교체 · `--detect-every` 노브 · MPS 디바이스 명시 지정 · `SlotManager`(타일 고정) · `Presence`(소실 홀드앤와이드) · CoreML은 불필요 판정 | 4K 합성 영상 실측: `detect_every=2`+MPS **39.6fps** (목표 30fps 통과) | M0 | PLAN.md Phase1(나머지)+Phase2, 상세: [next.md](next.md) |
| M2 | 패키징 전환 | ✅ 완료(2026-07-04) — `pyproject.toml`+콘솔 진입점(`reframe` 1개) · `Brewfile`(ffmpeg/mediamtx) · 모델 가중치(`yolov8n.pt`) 해시 고정, `requirements.txt` 통합 | `pip install -e .` 후 `reframe --self-test` 동작 | M1 (안정화된 코드를 패키지화) | INFRA-PLAN.md §10, 상세: [next.md](next.md) |
| M3 | 송출 PoC (1채널) | 🟡 거의 완료(2026-07-04) — RTSP·NDI 두 경로 모두 실동작 확인(`output.py`에 `RTSPPublisher`+`NDIPublisher`, UDP+B프레임0 튜닝), mDNS 디스커버리~OBS 화면 확인까지 완료. 실사용 중 버그 2건 발견·수정(줌 클램프, One Euro min_cutoff). **줌/패닝 시각 검증도 실 4K 카메라(Elgato Cam Link 4K)로 완료** — SINGLE 모드 얼굴 클로즈업이 자세 변화에도 중심을 유지하며 크롭되는 것 확인. MULTI 모드 풀바디 줌은 이번 데스크 세팅의 짧은 카메라-피사체 거리 때문에 프레임 클램프에 걸림(코드 문제 아님 — 거리 확보되는 환경에서 재검증 필요). 오디오먹싱만 미착수 | RTSP·NDI 경로 동작 확인(완료) · 줌/패닝 육안 확인(완료, MULTI 풀바디는 조건부 재검증 남음) | M2 | UI-PLAN.md §4, 상세: [next.md](next.md) |
| M4 | 4채널 확장 + 컨트롤 서버 | encode 프로세스 4채널화 · FastAPI+WebSocket · 프리뷰 스트림+캔버스 오버레이(읽기 전용 콘솔) · 입력 소스/해상도 선택 API(`/api/sources`, `/api/input`) | 4채널 동시 송출 + 읽기 전용 콘솔에서 실시간 인물박스/프리뷰 확인 + UI에서 카메라·해상도 전환 확인 | M3 | UI-PLAN.md §2a, §6 (기존 step 2-3, 6) |
| M5 | 편집 UI 연동 | 크롭 편집(이동/리사이즈/삭제/추가/프리셋) API 연동 · 트래킹 바인딩+소실 처리 연동 | [mockup/index.html](../mockup/index.html)에서 검증된 인터랙션이 실제 파이프라인을 그대로 조작 | M4 | UI-PLAN.md §6 (기존 step 4-5) |
| M6 | 서비스화 | launchd plist 4종+`install-services.sh` · 채널 배치 상태 영속화(`state.json`) · 로그 로테이션 | 재부팅 후 자동 기동, 마지막 채널 배치 복원 | M5 | INFRA-PLAN.md §5, §11 |
| M7 (옵션) | 확장 | BoT-SORT+ReID · NDI(분리형 시나리오 B) · 4채널 동시 녹화 | 필요 발생 시에만 착수 | M6 | PLAN.md §3.2 / INFRA-PLAN.md §1(B) / PLAN.md Phase3 |

## 지금 다음 액션

M0~M3 핵심 검증 끝났다(4K 카메라 확보로 줌/패닝 검증까지 완료). 남은 건 오디오 먹싱(캡처카드
필요)과 M4(컨트롤 서버) 착수뿐 — 상세는 [next.md](next.md) 참조.
