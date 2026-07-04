#!/usr/bin/env bash
# M6 서비스화: mediamtx + reframe-server를 launchd user agent로 등록 (INFRA-PLAN §5/§8/§10).
#
# 단일 프로세스 설계라 서비스는 둘뿐이다 - reframe-server 하나가 캡처+추론 파이프라인, HTTP/WS
# API, 채널별 ffmpeg 인코더를 전부 소유한다(원래 계획서의 4-프로세스 분리는 유예됐고 안 만들었다).
# 기동 순서(mediamtx 먼저)는 강제하지 않는다 - reframe-server의 퍼블리셔 재시도 로직이
# mediamtx가 늦게 떠도 알아서 다시 붙는다(output.py/ChannelOutputs).
#
# plist는 커밋하지 않고 여기서 생성한다 - venv/mediamtx/ffmpeg/레포 경로가 머신마다 달라서
# (카메라를 인덱스가 아니라 썸네일로 고르는 것과 같은 이유). 배포값은 env로 오버라이드:
#   REFRAME_SRC=0 REFRAME_RTSP_BASE=rtsp://localhost:8554/out REFRAME_NDI_BASE=reframe-out \
#   REFRAME_HOST=127.0.0.1 REFRAME_PORT=8000 scripts/install-services.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UID_NUM="$(id -u)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$HOME/Library/Logs/reframe"

SRC="${REFRAME_SRC:-0}"
RTSP_BASE="${REFRAME_RTSP_BASE:-rtsp://localhost:8554/out}"
NDI_BASE="${REFRAME_NDI_BASE:-reframe-out}"
HOST="${REFRAME_HOST:-127.0.0.1}"
PORT="${REFRAME_PORT:-8000}"

REFRAME_BIN="$REPO_ROOT/.venv/bin/reframe-server"
[ -x "$REFRAME_BIN" ] || REFRAME_BIN="$(command -v reframe-server || true)"
MEDIAMTX_BIN="$(command -v mediamtx || true)"
FFMPEG_BIN="$(command -v ffmpeg || true)"

for name in MEDIAMTX_BIN REFRAME_BIN FFMPEG_BIN; do
  if [ -z "${!name}" ] || [ ! -x "${!name}" ]; then
    echo "error: ${name%_BIN} not found - run 'brew bundle' and 'pip install -e .' (in a venv) first" >&2
    exit 1
  fi
done

# launchd hands processes a bare PATH (/usr/bin:/bin:...) that excludes Homebrew, but
# reframe-server spawns 'ffmpeg' by name (output.py) - without ffmpeg's dir on PATH every
# RTSP channel silently fails to encode. Put Homebrew's bin (ffmpeg/mediamtx live there)
# on the service's own PATH.
BREW_BIN="$(dirname "$FFMPEG_BIN")"

mkdir -p "$AGENTS" "$LOGS"

write_plist() {  # $1=label  $2=env-PATH-or-empty  rest=program+args
  local label="$1" envpath="$2"; shift 2
  local plist="$AGENTS/$label.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>${label}</string>"
    echo '  <key>ProgramArguments</key><array>'
    for a in "$@"; do echo "    <string>${a}</string>"; done
    echo '  </array>'
    echo "  <key>WorkingDirectory</key><string>${REPO_ROOT}</string>"
    if [ -n "$envpath" ]; then
      echo '  <key>EnvironmentVariables</key><dict>'
      echo "    <key>PATH</key><string>${envpath}</string>"
      echo '  </dict>'
    fi
    echo '  <key>RunAtLoad</key><true/>'
    echo '  <key>KeepAlive</key><true/>'
    echo '  <key>ThrottleInterval</key><integer>5</integer>'
    echo "  <key>StandardOutPath</key><string>${LOGS}/${label}.out.log</string>"
    echo "  <key>StandardErrorPath</key><string>${LOGS}/${label}.err.log</string>"
    echo '</dict></plist>'
  } > "$plist"
  launchctl bootout "gui/${UID_NUM}" "$plist" 2>/dev/null || true
  launchctl bootstrap "gui/${UID_NUM}" "$plist"
  echo "loaded ${label}"
}

write_plist com.reframe.mediamtx "" "$MEDIAMTX_BIN" "$REPO_ROOT/mediamtx.yml"
write_plist com.reframe.server "${BREW_BIN}:/usr/bin:/bin:/usr/sbin:/sbin" \
  "$REFRAME_BIN" --src "$SRC" \
  --rtsp-out-base "$RTSP_BASE" --ndi-out-base "$NDI_BASE" \
  --host "$HOST" --port "$PORT"

echo
echo "done."
echo "  logs:    $LOGS/com.reframe.*.log"
echo "  console: http://${HOST}:${PORT}"
echo "  status:  launchctl print gui/${UID_NUM}/com.reframe.server | grep -i state"
echo "  stop:    scripts/uninstall-services.sh"
