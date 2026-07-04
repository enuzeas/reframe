# walkthrough — 지금 상태로 돌려보기

기획 문서 4개(PLAN/UI-PLAN/INFRA-PLAN/ROADMAP)는 앞으로 만들 것에 대한 문서고, 이 파일은
**오늘 저장소에 있는 걸로 실제로 뭘 해볼 수 있는지**만 다룬다.

## 지금 존재하는 것

| 것 | 실체 | 안 되는 것 |
|---|---|---|
| [reframe.py](../reframe.py) | 4K→HD 3모드 크롭 CLI 진입점 (cv2 창에 2×2 프리뷰, `--rtsp-out`/`--ndi-out`으로 1채널 송출도 가능). 기능별로 [smoothing.py](../smoothing.py)/[geometry.py](../geometry.py)/[detection.py](../detection.py)/[tracking.py](../tracking.py)/[modes.py](../modes.py)/[display.py](../display.py)/[output.py](../output.py)로 분리돼 있음 | 4채널 동시 송출, 웹 컨트롤, 오디오 먹싱 — 이건 `reframe-server` 몫 |
| [server.py](../server.py) + [channels.py](../channels.py) | `reframe-server` — FastAPI 컨트롤 서버. 캡처+추론 루프를 백그라운드 스레드로 돌리며 동적 채널(최대 4) RTSP/NDI 송출 + MJPEG 프리뷰(원본 다운스케일) + WebSocket 오버레이 + 채널 CRUD/프리셋/트래킹 바인딩/입력 소스 전환 API | 오디오, 프로세스 분리(M6) |
| [console/index.html](../console/index.html) | `reframe-server`용 편집 콘솔 — 크롭 드래그/리사이즈, 인물 클릭 바인딩, 트래킹/줌/스무딩, 프리셋, 채널 추가/삭제, 카메라/해상도 선택 | 실제 마우스 조작은 이번 세션에 브라우저로 직접 확인 안 함(API 경로만 검증) |
| [mockup/index.html](../mockup/index.html) | UI-PLAN.md 인터랙션을 처음 검증했던 정적 목업(더미 인물, 시뮬레이션 데이터) — `console/index.html`이 실배선판이 된 뒤로는 참고용 | 실제 파이프라인과 연결 안 됨(원래도 그 목적이 아니었음) |
| [docs/pdf/UI-PLAN.pdf](pdf/UI-PLAN.pdf) | UI-PLAN.md를 인쇄한 스냅샷 | 최신 UI-PLAN.md 수정을 반영 안 함 — 필요하면 재출력 |

## 1. 파이프라인 프로토타입 돌리기

```bash
cd /Users/enujes/Sync/dev/reframe
source .venv/bin/activate   # 이미 만들어져 있음
pip install -e .            # pyproject.toml 기준 설치, reframe 명령 등록 (M2)
reframe --self-test         # 로직 자체 검증 (카메라/영상 없이)
```

카메라나 영상 파일로 실제 감지/추적을 보려면:

```bash
reframe --src 0              # 웹캠 (인덱스 0)
reframe --src some_4k.mp4    # 4K 영상 파일 (라이브 시뮬레이션)
```

(`python reframe.py ...`로 직접 실행해도 여전히 동작한다 — `reframe`은 그 위에 얹은 콘솔 진입점일 뿐.)

창이 뜨면 `1`/`2`/`3`으로 MULTI·QUAD+TRACK·SINGLE 모드 전환, `q`로 종료.
첫 실행 시 `yolov8n.pt` 가중치를 자동 다운로드한다 (인터넷 필요, 최초 1회).

**웹캠 인덱스는 기기마다 다르고 `0`이 원하는 카메라가 아닐 수 있다** — OBS Virtual
Camera·Camo·xpression 같은 가상 카메라가 설치돼 있으면 순서가 밀린다. 실제 장치명은
`ffmpeg -f avfoundation -list_devices true -i ""`로 확인 가능하지만, **OpenCV의
enumeration 순서는 이것과 다를 수 있다** — 확실하게 하려면 각 인덱스로 프레임 한 장씩
저장해서 눈으로 대조할 것.

**감지 박스(bounding box)는 화면에 안 그려진다** — `composite()`는 텍스트 라벨만 찍고
감지 박스 오버레이는 없다 (그건 UI-PLAN.md가 설계한 미래 웹 콘솔의 몫). 트래킹이 되는지는
크롭이 인물을 따라가는지로 확인한다. 실제 얼굴로 확인 결과: 박스 없이도 트래킹 잘 됨 —
M0의 마지막 미검증 항목(실제 인물 추적 정확도)이었던 게 여기서 검증 완료.

## 1b. RTSP로 OBS에 송출해보기 (M3)

```bash
brew bundle          # ffmpeg, mediamtx 설치 (최초 1회)
mediamtx /tmp/mediamtx.yml   # paths: all_others: 한 줄짜리 설정 파일 필요 (기본 빈 설정은 400/404로 막힘)
reframe --src 3 --mode 1 --no-preview --rtsp-out rtsp://localhost:8554/out1
```

OBS에서 **Media Source**(Browser Source 아님) 추가 → 로컬 파일 대신 입력 URL에
`rtsp://localhost:8554/out1` 입력. 왜 Browser Source/WebRTC가 아니라 Media Source/RTSP인지는
[UI-PLAN.md §4](UI-PLAN.md) 참조 — 내부 신호라 WebRTC의 지터버퍼 지연을 낼 이유가 없다.

**알려진 한계 (2026-07-04 실측)**: 노트북 웹캠은 세로 화각이 좁아 정상 작업 거리에서도
사람이 프레임의 97%를 차지 — 줌/패닝 효과가 안 보이는 게 정상이다 (코드 버그 아님, 크롭
여백 자체가 없음). 실제 4K 카메라로 테스트하기 전까지는 감지 박스가 잘 따라오는지만
확인하면 된다. 상세: [next.md](next.md) M3 섹션.

## 1c. NDI로 OBS에 송출해보기 (M3, RTSP 대안)

원래 계획은 Syphon이 1순위였지만(UI-PLAN.md §4), 이 머신엔 Syphon 플러그인이 없고 NDI
쪽(obs-ndi 플러그인 + NDI SDK)은 이미 설치돼 있어 NDI로 먼저 검증했다.

```bash
reframe --src 3 --mode 1 --no-preview --ndi-out reframe-out1
```

OBS에서 **NDI Source**(obs-ndi 플러그인 제공) 추가 → 소스 목록에서 `reframe-out1` 선택.
RTSP와 동시에 켜도 된다 (`--rtsp-out`과 `--ndi-out`을 같이 넘기면 둘 다 송출).

NDI SDK(`libndi.dylib`)는 brew로 안 깔린다 — proprietary 배포판이라 [NDI Tools](https://ndi.video/tools/)로
따로 설치해야 한다(이 머신은 이미 설치돼 있음). `cyndilib`(pip 패키지)는 그 SDK를 링크만 한다.

## 1d. 컨트롤 서버로 4채널 동시 송출 + 편집 콘솔 (M4+M5)

```bash
mediamtx /tmp/mediamtx.yml   # RTSP 쓸 거면 (1b 참고)
reframe-server --src 0 --mode 1 \
  --rtsp-out-base rtsp://localhost:8554/out \
  --ndi-out-base reframe-out
```

`out1..out4`(RTSP) / `reframe-out1..4`(NDI) 채널이 각각 독립적으로 뜬다(1~4개, 채널을
추가/삭제하면 그만큼 늘거나 준다) — OBS에서 채널당 하나씩 소스로 추가하면 동시에 받을 수
있다(1b/1c와 같은 방식).

브라우저로 `console/index.html`을 열면(정적 파일이라 `open console/index.html`로 바로 열어도
되고, `/api/*` 호출은 절대경로라 `http://localhost:8000`을 향한다) 라이브 프리뷰(원본
다운스케일) 위에 감지 박스+채널 크롭 사각형 오버레이가 보인다. **이제 편집 가능**:

- 크롭 사각형 드래그로 이동, 모서리로 리사이즈(트래킹 OFF 채널만)
- 트래킹 ON인데 대상 미지정 채널이 있으면, 프리뷰의 인물 점선 박스를 클릭해 바인딩
- 카드에서 트래킹 토글·줌 프리셋(수동/풀샷/웨이스트업/페이스)·스무딩 슬라이더·삭제
- 하단 MULTI/QUAD/SINGLE 버튼으로 채널 4개 일괄 재구성
- 최대 4채널, "+ 프레임 추가"로 새 채널 생성

API로 직접 조작하고 싶으면:

```bash
curl localhost:8000/api/channels
curl -X PATCH localhost:8000/api/channels/1 -H 'Content-Type: application/json' -d '{"target_id": 7}'
curl -X POST localhost:8000/api/preset/single
```

`reframe-server --self-test`로 카메라 없이 채널 CRUD + 렌더 로직 회귀 테스트를 빠르게 돌릴 수 있다.

## 2. UI 목업 열어보기

```bash
open /Users/enujes/Sync/dev/reframe/mockup/index.html
```

브라우저에서 바로 열린다. 더미 인물 3명이 움직이고, 트래킹 토글→인물 클릭→바인딩,
프레임 드래그/리사이즈, 프리셋 버튼, 채널 추가/삭제까지 전부 클릭해볼 수 있다.
실제 파이프라인 데이터는 아니고 UI-PLAN.md §2~3 인터랙션 검증용.

## 3. 문서 지도 — 뭘 볼 때 뭘 열어야 하나

| 궁금한 것 | 열 문서 |
|---|---|
| 감지/추적/스무딩 기술 선정 이유 | [PLAN.md](PLAN.md) |
| 화면 구성, 크롭 편집 인터랙션, 송출 프로토콜(Syphon/NDI/RTSP) | [UI-PLAN.md](UI-PLAN.md) |
| 배포 시나리오, 프로세스, 포트, 장애 대응, 패키징 | [INFRA-PLAN.md](INFRA-PLAN.md) |
| 전체 작업 순서와 마일스톤 | [ROADMAP.md](ROADMAP.md) |
| **지금 당장 뭘 코딩해야 하나** | [next.md](next.md) |

## 4. 아직 없는 것 (착각하지 말 것)

- 오디오 먹싱 — 캡처카드 오디오 입력 필요, 미착수(M3 나머지)
- Syphon 연동 — obs-syphon 플러그인 없음, NDI로 대체 완료(§1c)
- launchd 서비스, pipeline/api 프로세스 분리 — 미착수(M6, INFRA-PLAN.md §2 각주)
- `console/index.html`의 드래그/리사이즈를 실제 마우스로 조작해보는 육안 확인 — API/백엔드
  경로는 curl로 검증했지만 브라우저 조작 자체는 이번 세션에 직접 해보지 않음

`pyproject.toml` 패키징(M2), RTSP·NDI 1채널 송출(M3), 줌/패닝 육안 검증(M3, 실 4K 카메라로
완료), 4채널 컨트롤 서버(M4), 크롭 편집·트래킹 바인딩 API 연동(M5)은 전부 완료됐다 —
위 §1~§1d가 그 결과.
