# reframe 인프라 기획서 — 배포 토폴로지·프로세스·네트워크·운영

작성일: 2026-07-03 · 상태: 기획 · 상위 문서: [PLAN.md](PLAN.md) · [UI-PLAN.md](UI-PLAN.md)

기능/UI 설계는 앞선 두 문서에서 끝났다. 이 문서는 **그걸 실제로 어디서, 어떤 프로세스로,
어떤 포트로 돌리고, 뭐가 죽었을 때 어떻게 아는가**를 다룬다.

## 1. 배포 토폴로지 (3가지 시나리오)

동일 요구사항이라도 "OBS가 어디서 도는가"에 따라 인프라가 달라진다. UI-PLAN.md §4의
송출 선정 매트릭스가 여기서 물리 배치로 구체화된다.

### A. 올인원 — 캡처·파이프라인·OBS가 한 대의 Mac

```
┌─────────────────────────── Mac (Apple Silicon) ───────────────────────────┐
│  [UVC 4K30 캡처카드] ──USB── [reframe 파이프라인]                          │
│                                   │                                        │
│                          Syphon(GPU 텍스처, 로컬 전용)                     │
│                                   ▼                                        │
│                              [OBS Studio] ── obs-syphon Source × 4         │
│                                   │                                        │
│                          [FastAPI 컨트롤 서버 :8000] ← 같은 머신 브라우저   │
└─────────────────────────────────────────────────────────────────────────────┘
```

- 가장 단순, 지연 최저(§UI-PLAN §4 지연예산의 "합계" 항목보다도 낮음 — 인코딩 자체가 없음).
- 네트워크 의존 없음 → 이 문서의 "장애 모드" 상당수가 원천 제거됨.
- 단점: Mac 한 대가 캡처+추론(GPU/ANE)+인코딩(OBS 자체 송출용)+합성을 전부 부담.

### B. 분리형 — 파이프라인 Mac과 OBS Mac이 같은 LAN의 다른 머신

```
┌──── 파이프라인 Mac ────┐        LAN (유선 권장, 1GbE+)        ┌──── OBS Mac ────┐
│ [캡처카드]              │                                     │                  │
│ [reframe 파이프라인]    │──NDI(멀티캐스트/유니캐스트)──────▶ │ [OBS + NDI Source]│
│ [FastAPI :8000]         │──HTTP(컨트롤 UI, 원격 접속)────────▶│ (다른 노트북 등)  │
└─────────────────────────┘                                     └──────────────────┘
```

- 캡처/추론 부하를 OBS/송출 부하와 분리 — 파이프라인 Mac에 리소스를 몰아줄 수 있음.
- NDI는 mDNS(Bonjour)로 디스커버리 → 같은 서브넷/VLAN 안에 있어야 함, mDNS 차단하는
  관리형 네트워크(회사 게스트망 등)에서는 안 잡힘 → 방송 현장 Wi-Fi 라우터는 별도 지참 권장.

### C. 모니터링 확장 — 스태프가 폰/태블릿 브라우저로 프리뷰만 확인

```
(시나리오 A 또는 B) ──MediaMTX WebRTC(:8889)──▶ 스태프 폰/태블릿 브라우저 (읽기 전용)
```

- A/B 위에 얹는 선택적 레이어. OBS 송출과 무관하게 컨트롤 서버 옆에 MediaMTX를 하나 띄워
  프리뷰 채널만 WebRTC로 공개. UI-PLAN.md §4에서 "예외 상황"으로 분류한 경로가 이것.

**기본값은 A.** 리허설/1인 운영에서는 A로 시작하고, 캡처+추론 부하가 실측상 OBS 인코딩과
경합하면 B로 분리한다 (§7 모니터링 지표가 분리 여부의 판단 근거).

## 2. 프로세스 인벤토리

| 프로세스 | 역할 | 기술 | 포트 | 실패 시 영향 |
|---|---|---|---|---|
| `reframe-pipeline` | 캡처→감지→추적→크롭→합성 | Python (YOLO11+ByteTrack, PLAN.md §2) | — | 전 채널 정지 (단일 장애점) |
| `reframe-api` | 컨트롤 REST/WS, 프리셋, 채널 CRUD | FastAPI (UI-PLAN.md §5) | 8000 | UI 조작 불가, **송출은 계속됨**(별도 프로세스) |
| `reframe-encode-{1..4}` | 채널별 h264 인코딩 | ffmpeg + VideoToolbox | — | 해당 채널만 정지 |
| `mediamtx` | RTSP/WebRTC 릴레이 허브 | MediaMTX 단일 바이너리 | 8554(RTSP), 8889(WebRTC), 8888(HLS, 미사용시 비활성) | RTSP/브라우저 수신 전체 정지, Syphon/NDI 경로는 무관 |
| `ndi-sender` (시나리오 B) | NDI 송출 | ndi-python 또는 ffmpeg NDI 출력 | UDP 동적(NDI 프로토콜) | OBS(원격) 수신 끊김 |
| `obs-studio` | 최종 스위칭/송출 | OBS + obs-syphon/obs-ndi 플러그인 | — | 방송 자체 중단 — 이 시스템 범위 밖, 별도 이중화 검토 |

설계 원칙: **`reframe-pipeline`이 죽으면 전부 멈추지만, `reframe-api`가 죽어도 이미 떠 있는
송출(Syphon/NDI/RTSP)은 마지막 상태로 계속 나간다.** 컨트롤 서버와 미디어 파이프를 같은
프로세스에 넣지 않는 이유가 이것 — 편집 UI가 뻗어도 방송이 끊기면 안 됨.

**M4 구현 시 유예(2026-07-04)**: 위 분리는 M6(서비스화)으로 미루고, M4의 첫 컨트롤 서버
(`server.py`/`reframe-server`)는 FastAPI(uvicorn)와 캡처·추론 루프를 백그라운드 스레드로
한 프로세스 안에서 돌린다 — 지금은 리허설/개발 단계라 API 크래시 시 방송 지속 요구가
아직 실제로 걸리지 않음(이 문서 §10의 "조건이 성립하지 않으면 미리 만들지 않는다" 원칙과
동일). 운영 규모가 커지거나 이 보호가 실제로 필요해지면 그때 프로세스를 분리한다.

## 3. 네트워크 및 포트

| 포트 | 프로토콜 | 용도 | 노출 범위 |
|---|---|---|---|
| 8000 | HTTP/WS | 컨트롤 콘솔 (UI-PLAN.md §5 API) | LAN 내부만 |
| 8554 | RTSP | OBS Media Source 연결용 (기본 송출) | LAN 내부만 |
| 8889 | HTTP/WebRTC | Browser Source / 모니터링용 (예외 경로) | LAN 내부만, 필요시에만 기동 |
| 5353 | UDP mDNS | NDI 디스커버리 | LAN 내부만, 라우터가 mDNS를 막으면 NDI 자체가 실패 |
| — | Syphon | 로컬 IPC (GPU 텍스처 공유) | 같은 머신 프로세스 간, 네트워크 미사용 |

- **모든 포트는 인터넷에 노출하지 않는다.** 공유기/방화벽에서 포트포워딩 대상에 포함 금지.
  원격 모니터링이 꼭 필요하면 VPN(Tailscale 등) 안에서만 8889를 열 것 — WebRTC라도 공인
  인터넷 직접 노출은 별도 위협 모델(인증 없음)이라 범위 밖으로 명시.
- 대역폭: 시나리오 B의 NDI는 채널당 비압축에 가까운 스트림 특성상 1080p60 기준 대략
  100–150Mbps/채널. 4채널 동시 전송 시 1GbE 한 회선을 거의 채우므로, **분리형은 유선
  1GbE 이상 + 스위치(공유기 Wi-Fi 경유 금지)**가 필수 조건이다.

## 4. 하드웨어 요구사항

| 구성요소 | 최소 | 권장 | 근거 |
|---|---|---|---|
| Mac (파이프라인) | M3, 16GB | M4 Pro 이상, 32GB+ | PLAN.md §3.4 — CoreML 추론 + 4채널 동시 인코딩 세션 |
| 캡처카드 | UVC 4K30 (HDMI 2.0) | HDMI 2.1 지원 카드 (4K60 필요 시) | PLAN.md §3.5 |
| 네트워크 (시나리오 B) | 1GbE 유선 | 2.5GbE, 스위치 경유 | §3 대역폭 계산 |
| 저장공간 (4채널 동시 녹화 시) | — | NVMe SSD, 채널당 ~25GB/h(h264) 기준 여유 있게 | PLAN.md Phase 3 "4채널 동시 녹화" |

## 5. 프로세스 관리 및 자동 복구

- 전부 `launchd` user agent로 등록 (`~/Library/LaunchAgents/com.reframe.*.plist`),
  `KeepAlive: true`로 크래시 시 자동 재기동. 재기동 폭주 방지를 위해 `ThrottleInterval` 설정.
- 기동 순서 의존성: `mediamtx` → `reframe-pipeline` → `reframe-encode-*` → `reframe-api`.
  launchd 자체는 순서를 보장하지 않으므로 각 서비스가 의존 대상에 헬스체크 재시도 로직을 갖는다
  (예: encode 프로세스는 mediamtx RTSP publish 실패 시 backoff 재시도).
- ponytail: 이 순서 문제는 launchd의 근본 한계라 셸 스크립트로 순차 기동 래퍼 하나만 두고
  넘어간다. 신뢰성이 실제로 문제가 되면 그때 launchd 대신 프로세스 슈퍼바이저(예: overmind)로 승격.

## 6. 모니터링 / 헬스체크

| 지표 | 수집 위치 | 경고 임계치 | 대응 |
|---|---|---|---|
| 파이프라인 fps | `reframe-pipeline` (UI 상단바 표시, PLAN.md 참고) | < 25fps 지속 3초 | 감지 주기 하향(DETECT_EVERY↑) 또는 시나리오 B 분리 검토 |
| 엔드투엔드 지연 추정 | 프레임 타임스탬프 역산 | RTSP > 200ms / WebRTC > 500ms | 인코더 프리셋·네트워크 확인 |
| 캡처카드 연결 상태 | `cv2.VideoCapture.isOpened()` 폴링 | 프레임 미도착 1초 | 마지막 프레임 홀드 + UI에 "입력 끊김" 배너, 재연결 자동 시도 |
| 채널별 송출 상태 | ffmpeg 프로세스 exit code / MediaMTX 세션 목록 | 프로세스 종료 | launchd 자동 재기동 + UI 카드 "오프라인" 표시(UI-PLAN.md §3 상태 표시) |
| 디스크 여유공간 (녹화 사용 시) | 주기적 `df` 체크 | < 10GB | 녹화만 먼저 중단, 라이브 송출은 유지 |
| GPU/ANE 사용률 | `powermetrics` 또는 Ultralytics 자체 타이밍 | 지속 100% + fps 저하 동반 | 인코딩을 CPU(libx264)로 폴백하거나 채널 수 축소 |

컨트롤 콘솔 상단바(UI-PLAN.md §2)에 이미 fps/지연 표시 자리가 있으므로, 1차 모니터링은 별도
대시보드 없이 그 화면으로 충분하다. 채널 수·운영 규모가 커지면 그때 Prometheus 등 승격.

## 7. 장애 모드 및 대응

| 장애 | 감지 | 자동 대응 | 수동 대응 |
|---|---|---|---|
| 캡처카드 케이블 뽑힘/전원 문제 | `isOpened()` false | 마지막 프레임 홀드, 재연결 폴링 | 케이블/카드 점검 |
| 인물 감지 대상 완전 이탈(트래킹 채널) | 트랙 ID 소실 (PLAN.md §2 스무딩 로직) | 마지막 위치 홀드 (UI-PLAN.md 트래킹 표 "인물 소실") | 대상 재바인딩 또는 프리셋 재적용 |
| ffmpeg 인코딩 프로세스 크래시 | exit code 감시 | launchd 재기동 | 반복 크래시 시 로그 확인(코덱/해상도 불일치 등) |
| MediaMTX 다운 | RTSP/WebRTC 연결 전체 끊김 | launchd 재기동 | Syphon 경로(시나리오 A)는 이 장애와 무관 — 평상시 Syphon 우선 이유이기도 함 |
| NDI mDNS 미발견 (시나리오 B) | OBS에서 NDI Source 목록에 안 뜸 | — (네트워크 계층 문제라 자동 복구 어려움) | 같은 서브넷 확인, 회사망이면 전용 라우터로 전환 |
| 컨트롤 서버(FastAPI) 크래시 | HTTP 헬스체크 실패 | launchd 재기동 | 마지막 송출 상태는 유지됨(§2 설계 원칙) — 급하지 않음 |
| 4채널 동시 인코딩으로 열 스로틀링 | fps 지표 저하 + 온도 상승 | — | 채널 수 축소 또는 시나리오 B로 분리 |

## 8. 저장소 / 설정 영속성

- 채널 배치(좌표/트래킹 상태/줌 프리셋)는 `~/Library/Application Support/reframe/state.json`에
  주기 저장(변경 시 debounce 2초) — 재시작 후 마지막 구성 복원.
- 녹화본(Phase 3 옵션)은 별도 볼륨에 채널별 파일로 저장, 이름 규칙 `out{n}_YYYYMMDD_HHMMSS.mp4`.
- 로그는 launchd 표준 stdout/stderr 리다이렉트로 `~/Library/Logs/reframe/*.log`, 크기 기준
  로테이션(예: 100MB) — 무한 증가 방지.

## 9. 보안

- 컨트롤 콘솔(8000)과 미디어 포트 전부 인증 없이 LAN 오픈 — **신뢰된 내부망 전제**.
  방송 현장처럼 외부인이 같은 Wi-Fi에 붙을 수 있는 환경이면 최소한 컨트롤 콘솔에 기본 인증
  (Basic Auth 또는 접속 IP 화이트리스트)을 추가한다. 이 문서 기준 v1 범위에서는 미포함,
  실제 배포 환경이 정해지면 재검토.
- 인터넷 노출 금지는 §3에서 이미 명시 — 재차 강조: 이 스택 전체가 "내부 신호" 전제 위에
  설계됐으므로(UI-PLAN.md §4 개정 배경) 외부 노출은 설계 목표 자체를 벗어난다.

## 10. 패키징

**대상은 "신뢰된 내부망 안, 우리가 관리하는 Mac 몇 대"** (§1 시나리오 A/B 범위) — 통제 밖
머신이나 비개발자 운영자에게 넘길 계획이 없다. 이 전제에서 packaging은 "재현 가능한 설치
스크립트 조합"이면 충분하고, 아래는 의도적으로 뺀다.

| 뺀 것 | 이유 |
| --- | --- |
| Docker | GPU/ANE(CoreML/MPS), USB 캡처카드, Syphon(로컬 IPC)까지 macOS 네이티브 리소스 접근이 필요 — 컨테이너가 이걸 막거나 크게 번거롭게 만듦 |
| PyInstaller/py2app 단일 `.app` | 코드서명·공증(notarization)까지 얹어야 Gatekeeper를 통과 — 내부 도구 하나에 비해 비용 과다. 모델 가중치·OpenCV 바이너리 번들링도 추가 부담 |
| Homebrew tap 공개 배포 | 배포 대상이 우리가 관리하는 머신뿐이라 공개 formula 유지보수가 불필요 |

**대신 쓰는 조합:**

| 계층 | 방식 | 비고 |
| --- | --- | --- |
| Python 코드 | `pyproject.toml`(setuptools, `py-modules`)으로 패키지화, 콘솔 진입점 `reframe` 1개 등록 | `pip install -e .`로 설치. `reframe-pipeline`/`reframe-api` 분리는 그 프로세스들이 실제로 생기는 M3/M4 이후에 나눔(코드는 여전히 형제 모듈 파일들, `src/` 레이아웃도 그때 재검토) |
| 외부 도구(ffmpeg, mediamtx) | 저장소 루트 `Brewfile` + `brew bundle` | §11 체크리스트의 수동 나열을 명령어 한 줄로 대체, 버전 고정. 아직 어떤 코드도 안 씀(M3에서 사용 시작) |
| 서비스 등록 | `deploy/launchd/*.plist` 템플릿 + `scripts/install-services.sh` | §5의 launchd 구조를 스크립트로 자동화 — M6에서 실제 서비스(pipeline/encode/api)가 생긴 뒤 작성 |
| 설정값 | `~/Library/Application Support/reframe/config.yaml` (레포에 기본값, 로컬에 오버라이드) | 채널 배치·캡처 장치 인덱스 등 머신별 값 분리(§8) — 채널/UI 백엔드가 생기는 M4~M5 이후에 의미가 있어 아직 안 만듦 |
| 모델 가중치 | `yolov8n.pt` sha256 `f59b3d83...fc83b36` (2026-07-04 다운로드분) | 계획 초안은 YOLO11n을 가정했지만 프로토타입 기본값은 `yolov8n.pt` — PLAN.md §3.1의 YOLO11n 전환은 아직 미착수, 전환 시 이 해시도 갱신할 것. 파일 자체는 `.gitignore`(`*.pt`)로 커밋 제외, Ultralytics가 첫 실행 시 자동 다운로드 |

업그레이드 경로: 운영 머신 수가 늘거나 비개발자가 설치해야 하는 상황이 실제로 생기면, 그때
Homebrew tap(`brew install reframe`) 또는 서명된 `.app`으로 승격한다 — 지금은 조건이
성립하지 않으므로 미리 만들지 않는다.

## 11. 설치 절차 체크리스트

```
[ ] git clone 후 pip install -e .  (pyproject.toml 진입점 등록, §10)
[ ] brew bundle  (Brewfile: ffmpeg, mediamtx, §10)
[ ] obs-syphon 플러그인 설치 (시나리오 A)  /  obs-ndi(DistroAV) 설치 (시나리오 B)
[ ] UVC 캡처카드 연결 확인: `system_profiler SPUSBDataType | grep -A5 Capture`
[ ] scripts/install-services.sh 실행 (launchd plist 4종 등록 및 로드)
[ ] 컨트롤 콘솔(http://localhost:8000) 접속 확인
[ ] OBS에서 채널 4개 소스 연결 확인 (시나리오별 프로토콜)
[ ] fps/지연 지표가 §6 임계치 이내인지 실측
```

## 12. 참고

- 송출 프로토콜 선정 근거는 [UI-PLAN.md §4](UI-PLAN.md) 참조 (이 문서는 그 결정을 물리
  배치·프로세스·운영 관점으로 구체화한 것).
- 파이프라인 기술 선정(감지/추적/추론 실행) 근거는 [PLAN.md §3](PLAN.md) 참조.
