# walkthrough — 지금 상태로 돌려보기

기획 문서 4개(PLAN/UI-PLAN/INFRA-PLAN/ROADMAP)는 앞으로 만들 것에 대한 문서고, 이 파일은
**오늘 저장소에 있는 걸로 실제로 뭘 해볼 수 있는지**만 다룬다.

## 지금 존재하는 것

| 것 | 실체 | 안 되는 것 |
|---|---|---|
| [reframe.py](../reframe.py) | 4K→HD 3모드 크롭 CLI 진입점 (cv2 창에 2×2 프리뷰, `--rtsp-out`으로 RTSP 송출도 가능). 기능별로 [smoothing.py](../smoothing.py)/[geometry.py](../geometry.py)/[detection.py](../detection.py)/[tracking.py](../tracking.py)/[modes.py](../modes.py)/[display.py](../display.py)/[output.py](../output.py)로 분리돼 있음 | 웹 UI, Syphon/NDI, 오디오 먹싱, 4채널 동시 송출 — 아직 1채널 RTSP까지만 |
| [mockup/index.html](../mockup/index.html) | UI-PLAN.md 인터랙션을 확인하기 위한 정적 목업 (더미 인물이 움직임) | 실제 파이프라인과 연결 안 됨 — 클릭하면 캔버스 안에서만 반응 |
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

- FastAPI 컨트롤 서버, Syphon/NDI 연동, 4채널 동시 송출, 오디오 먹싱 — 설계만 끝남(M3 나머지~M6)
- 줌/패닝 육안 검증 — 노트북 웹캠 화각 한계로 보류, 실 4K 카메라 필요(§1b)
- launchd 서비스 — 미착수(M6)
- 목업의 인터랙션과 실제 파이프라인 연결 — 미착수(M5)

`pyproject.toml` 패키징(M2)과 RTSP 송출 1채널(M3 일부)은 완료됐다 — 위 §1, §1b가 그 결과.
