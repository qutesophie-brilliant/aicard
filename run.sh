#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -f .env ] || [ ! -f .env.example ] || cp .env.example .env
python3 seed.py
# 무료 서버가 잠들지 않게 10분마다 자기 주소를 한 번 열어 본다(Render가 주는 주소가 있을 때만).
if [ -n "${RENDER_EXTERNAL_URL:-}" ]; then
  python3 - <<'EOF' &
import os, time, urllib.request
url = os.environ["RENDER_EXTERNAL_URL"].rstrip("/") + "/about"
while True:
    time.sleep(600)
    try:
        urllib.request.urlopen(url, timeout=30).read(64)
    except Exception:
        pass
EOF
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
