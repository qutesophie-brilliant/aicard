#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -f .env ] || [ ! -f .env.example ] || cp .env.example .env
python3 seed.py
# 공개 서버에서는 교육용 가상 제품 카드를 숨긴다.
if [ "${PUBLIC_DEPLOY:-}" = "1" ]; then
  python3 -c "import sqlite3; c=sqlite3.connect('data/aicard.sqlite3'); c.execute(\"update products set status='held' where is_sample=1 and status='approved'\"); c.commit()"
fi
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
