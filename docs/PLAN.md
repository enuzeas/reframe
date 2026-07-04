# reframe 기획서 — 실시간 4K → 4×HD AI 리프레이밍

작성일: 2026-07-03 · 상태: 기획 (리서치 완료, 구현 전)

## 1. 목표

4K(3840×2160) 라이브 입력 1개를 받아, 인물을 감지·추적하면서 HD(1920×1080) 화면 4개를
실시간으로 생성한다. VVERTIGO(KBS)의 핵심 파이프라인을 개인/소규모 제작 규모로 구현하는 것.

4K = HD×4 픽셀이므로 **트래킹 크롭은 업스케일 없이 원본 화질을 유지**한다 (클로즈업 제외).

### 출력 모드 (런타임 전환)

| 모드 | 구성 | 용도 |
|------|------|------|
| MULTI | 감지된 인물 최대 4명, 각자 추적 크롭 | 직캠 (VVERTIGO 방식) |
| QUAD+TRACK | 고정 사분면 3개 + 메인 인물 추적 1개 | 전경 감시 + 주피사체 |
| SINGLE | 인물 1명: 와이드 / 풀샷 / 웨이스트 / 페이스 | 1인 방송 멀티캠 연출 |

## 2. 파이프라인 아키텍처

```
[4K 입력] → [디코드/캡처] → [다운스케일 960px] → [감지+추적 (GPU/ANE)]
                │                                        │
                │                              [트랙별 궤적 스무딩]
                │                                        │
                └──── 4K 원본에서 크롭 ←── [크롭 윈도우 계산 (모드별)]
                              │
                    [4× HD 타일] → 프리뷰 / Syphon / NDI / 녹화
```

핵심 원칙: **감지는 저해상도, 크롭은 원본 해상도.** 4K 프레임 전체를 모델에 넣지 않는다.

## 3. 기술 선정 (리서치 근거)

### 3.1 인물 감지 — Ultralytics YOLO11n (person class)

- YOLO11은 멀티스트림 실시간 배포의 실용적 선택지. RT-DETR 계열이 군중/가림 장면에서
  정확도가 더 높지만 프레임당 지연이 커서 실시간 제약에 불리.
- 얼굴 전용 감지기(YuNet 1.6ms, SCRFD)는 더 빠르지만, 프레이밍에는 몸 전체 박스가 필요.
  → **1차: person 감지만. 얼굴은 person 박스 상단 비율 휴리스틱.**
  → 2차(옵션): 클로즈업 정밀도가 필요해지면 YuNet을 person 박스 내부에서만 실행.
- 무대처럼 가림이 심한 환경에서 문제가 생기면 RT-DETR로 교체 (Ultralytics API 호환).

### 3.2 추적 — ByteTrack (기본), BoT-SORT+ReID (업그레이드)

- ByteTrack: 빠르고 단순, 저신뢰 박스 2차 매칭으로 가림에 어느 정도 강함. 처리량 우선 선택.
- ID 스왑(비슷한 옷의 인물 교차)이 실제로 문제가 되면 BoT-SORT + `with_reid: True`.
  Ultralytics에서 YAML 한 줄 교체로 가능하므로 미리 만들 필요 없음.
- MULTI 모드의 "누가 어느 타일인가"는 트래커 ID 기반. 장기 이탈 후 재등장 시 동일인 보장은
  ReID 없이는 불가 → v1 한계로 명시.

### 3.3 궤적 스무딩 — One Euro Filter

- 실시간용 표준: 저속에서 지터 억제, 고속에서 랙 최소화하는 적응형 저역통과 필터.
  파라미터 2개(min_cutoff, beta)로 튜닝 단순.
- AutoFlip의 다항식 경로 최적화는 씬 단위 후처리 방식이라 라이브에 부적합.
- Kalman(등속 모델)은 차선책. 프로토타입의 EMA+데드존 → One Euro로 교체 예정.
- 추가 규칙: 크롭 윈도우 프레임 경계 클램프, 줌 변화율 제한, 씬 전환(입력 스위칭) 시 스냅.

### 3.4 추론 실행 — CoreML 익스포트 (Apple Silicon)

- M3 기준 YOLO11n: CPU 26ms → MPS 9ms → CoreML(ANE 활용) ~50fps, iOS 사례 85fps.
- 1차는 PyTorch+MPS로 시작(코드 단순), 프레임레이트가 부족하면
  `model.export(format="coreml")` 후 교체. NMS-free인 YOLO26n도 대안.
- 성능 예산: 4K 30fps 기준 프레임당 33ms. 감지 ~10ms + 크롭/리사이즈 4회 ~8ms + 출력.
  부족하면 감지를 2프레임에 1회로 낮추고 추적 보간 (`DETECT_EVERY` 노브).

### 3.5 입력 — UVC 4K30 캡처카드 (또는 파일 시뮬레이션)

- UVC 카드(Elgato Cam Link 4K, ASUS TUF CU4K30 등)는 macOS에서 드라이버 없이
  `cv2.VideoCapture(index)`로 바로 잡힘. **HDMI 2.0 카드는 캡처가 4K30 한계** — 60fps가
  필요하면 HDMI 2.1 카드 필요.
- DeckLink는 방송급 I/O가 필요할 때만 (SDK 통합 비용 큼). v1 범위 외.
- 개발/테스트는 4K 영상 파일로 라이브 파이프라인을 시뮬레이션.
- **카메라/해상도는 하드코딩이 아니라 UI에서 선택**한다 — 상세 UI/API 설계는
  [UI-PLAN.md §2a](UI-PLAN.md) 참조. §1의 "4K = HD×4라 크롭이 원본 화질을 유지한다"는
  전제는 **입력이 실제로 4K일 때만** 성립하므로, 더 낮은 해상도를 고르면 클로즈업 크롭에서
  업스케일이 발생함을 UI가 알려야 한다.
- **`cv2.VideoCapture.set()`은 실패해도 조용히 무시된다** — 요청한 해상도를 카메라가 지원 안
  하면 예외 없이 다른(보통 네이티브) 해상도를 그대로 전달한다(2026-07-04 FaceTime HD Camera
  실측: 3840×2160 set 요청 → 실제로는 1280×720 그대로 전달됨). 그래서 `set()` 뒤에는 반드시
  `get()`으로 실제 적용된 해상도를 재확인해 UI/로그에 노출해야 한다 — 요청값을 믿고 넘어가면
  안 됨.

### 3.6 출력 — 단계적

| 단계 | 출력 | 근거 |
|------|------|------|
| v1 | 2×2 프리뷰 창 + 선택적 4채널 mp4 녹화 | 검증용, 의존성 0 |
| v2 | **Syphon** (syphon-python, Metal 서버 4개) | macOS 로컬 앱 간 GPU 텍스처 공유, 지연 ~0. OBS/mimoLive/Resolume 수신 가능 |
| v3 | NDI (ndi-python) | 네트워크/타 머신 배포 필요 시. 압축+~16ms 지연 비용 있음 |

Syphon→가상카메라는 Syphon Webcam, Syphon→NDI는 NDISyphon으로 브리지 가능하므로
v1~v2만으로 대부분의 라이브 워크플로우에 연결된다.

### 3.7 기존 솔루션 재사용 검토 (만들지 않을 이유 확인)

| 후보 | 탈락 사유 |
|------|-----------|
| OBS Face Tracker | 1인 한정(다인 시 왔다갔다), dlib 구세대, 모드 개념 없음 |
| Google AutoFlip / pyautoflip | 후처리 전용(씬 단위 최적화), 라이브 불가, MediaPipe 버전은 지원 종료 |
| auto-vertical-reframe | 후처리 CLI, 단일 출력. 단, 프리셋/랭킹 설계는 참고 가치 있음 |
| 상용 (OBSBOT, HuddleCam EPTZ, Panasonic) | 하드웨어 종속, 4분할 멀티출력 없음 |

→ "실시간 + 다인 + 4채널 동시 출력" 조합을 제공하는 기존물이 없어 직접 구현이 정당.

## 4. 구현 단계

### Phase 1 — 코어 파이프라인 (프로토타입 존재: reframe.py)
- [x] 파일/카메라 입력 → YOLO+ByteTrack → 3모드 크롭 → 2×2 프리뷰
- [ ] EMA+데드존 → One Euro Filter 교체
- [ ] 감지 주기 노브(DETECT_EVERY) + fps 계측
- [ ] 4K 테스트 영상으로 실측 (목표: M시리즈에서 30fps)

### Phase 2 — 안정화
- [ ] MULTI 모드 타일-트랙 고정 할당 (슬롯 매니저, 타일 셔플 방지)
- [ ] 줌 변화율 제한, 인물 소실 시 홀드-앤-와이드 폴백
- [ ] CoreML 익스포트 벤치마크, 필요 시 교체

### Phase 3 — 웹 콘솔 + 웹 URL 송출 (상세: [UI-PLAN.md](UI-PLAN.md))

- [ ] MediaMTX + ffmpeg 채널별 WebRTC 송출 (OBS 브라우저 소스 URL, 오디오 포함 저지연)
- [ ] 웹 UI: 크롭 프레임 이동/리사이즈/삭제/추가 + 트래킹 바인딩
- [ ] (필요 시) Syphon/NDI 출력, 4채널 동시 녹화, BoT-SORT+ReID

## 5. 리스크

| 리스크 | 대응 |
|--------|------|
| 4K 디코드+감지+4크롭이 30fps 미달 | 감지 주기 하향, CoreML 전환, 프리뷰 해상도 축소 |
| 다인 교차 시 ID 스왑 (직캠 뒤바뀜) | BoT-SORT+ReID 승격, 수동 타일 고정 키 |
| UVC 카드 4K30 한계 (60fps 불가) | 요구사항 확인 후 HDMI 2.1 카드 or DeckLink |
| 클로즈업 업스케일 화질 | 4K 입력에선 감수, 8K 입력 지원은 범위 외 |

## 6. 참고 자료

- 감지/추적: [Ultralytics track mode](https://docs.ultralytics.com/modes/track), [MOT 2026 프로덕션 가이드](https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/multi-object-tracking-deepsort-bytetrack-ocsort), [RT-DETR vs YOLO11](https://docs.ultralytics.com/compare/rtdetr-vs-yolo11)
- 스무딩: [1€ Filter 논문](https://dl.acm.org/doi/10.1145/2207676.2208639), [AutoFlip 설계](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/)
- Apple Silicon 추론: [M3 YOLO 벤치마크](https://hexdocs.pm/yolo/macbook_air_m3.html), [CoreML export](https://docs.ultralytics.com/integrations/coreml), [Roboflow M4 벤치마크](https://blog.roboflow.com/putting-the-new-m4-macs-to-the-test/)
- 출력: [syphon-python](https://github.com/cansik/syphon-python), [ndi-python](https://github.com/buresu/ndi-python)
- 얼굴 감지(2차): [YuNet 논문](https://link.springer.com/article/10.1007/s11633-023-1423-y), [2025 얼굴감지 비교](https://learnopencv.com/what-is-face-detection-the-ultimate-guide/)
- 유사물: [obs-face-tracker](https://github.com/norihiro/obs-face-tracker), [auto-vertical-reframe](https://github.com/KazKozDev/auto-vertical-reframe), [pyautoflip](https://github.com/AhmedHisham1/pyautoflip)
