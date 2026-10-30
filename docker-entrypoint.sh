#!/bin/bash
# Bật Tor SOCKS nội bộ rồi chạy app với YTDLP_PROXY trỏ vào nó.
# Nếu YTDLP_PROXY đã được set từ ngoài (proxy trả phí/residential) thì bỏ qua Tor.
set -euo pipefail

TOR_ENABLED="${TOR_ENABLED:-true}"
TOR_SOCKS_HOST="${TOR_SOCKS_HOST:-127.0.0.1}"
TOR_SOCKS_PORT="${TOR_SOCKS_PORT:-9050}"
TOR_BOOTSTRAP_TIMEOUT="${TOR_BOOTSTRAP_TIMEOUT:-60}"
TOR_DATA_DIR="${TOR_DATA_DIR:-/var/lib/tor}"
TOR_LOG_FILE="${TOR_LOG_FILE:-/var/log/tor/notices.log}"

start_tor() {
  mkdir -p "$TOR_DATA_DIR" "$(dirname "$TOR_LOG_FILE")"
  chown -R debian-tor:debian-tor "$TOR_DATA_DIR" "$(dirname "$TOR_LOG_FILE")"
  chmod 700 "$TOR_DATA_DIR"

  # User debian-tor: tor tự hạ quyền (package tor tạo sẵn user này).
  # Một SocksPort không isolation => một circuit dùng chung cho cả youtube.com
  # và googlevideo.com, nên exit IP khớp giữa bước trích xuất và bước tải file.
  cat > /etc/tor/torrc.local <<EOF
User debian-tor
DataDirectory $TOR_DATA_DIR
SocksPort $TOR_SOCKS_HOST:$TOR_SOCKS_PORT
MaxCircuitDirtiness 600
Log notice stdout
EOF

  # Process substitution: $! là PID của tor, không phải của tee.
  tor -f /etc/tor/torrc.local > >(tee "$TOR_LOG_FILE") 2>&1 &
  TOR_PID=$!

  for _ in $(seq 1 "$TOR_BOOTSTRAP_TIMEOUT"); do
    grep -q "Bootstrapped 100%" "$TOR_LOG_FILE" 2>/dev/null && return 0
    kill -0 "$TOR_PID" 2>/dev/null || return 1
    sleep 1
  done
  return 1
}

if [ -n "${YTDLP_PROXY:-}" ]; then
  echo "[entrypoint] YTDLP_PROXY đã có (proxy ngoài), bỏ qua Tor"
elif [ "$TOR_ENABLED" = "true" ]; then
  if start_tor; then
    export YTDLP_PROXY="socks5h://${TOR_SOCKS_HOST}:${TOR_SOCKS_PORT}"
    echo "[entrypoint] Tor sẵn sàng, YTDLP_PROXY=${YTDLP_PROXY}"
  else
    echo "[entrypoint] CẢNH BÁO: Tor không khởi động được, chạy tiếp không proxy" >&2
  fi
fi

exec python main.py
