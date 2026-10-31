"""SQLite 저장소. 제품·문서·청크·카드·검토 이력."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  maker TEXT DEFAULT '',
  model TEXT DEFAULT '',
  category TEXT DEFAULT '',
  is_sample INTEGER DEFAULT 0,
  status TEXT DEFAULT 'draft',        -- draft | pending | approved | held
  created_at TEXT, updated_at TEXT, approved_at TEXT,
  reviewer_note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                 -- 판매페이지 | 설명서 | 사양표 | 공공DB | 기타
  title TEXT DEFAULT '',
  url TEXT DEFAULT '',
  fetched_at TEXT,
  text TEXT DEFAULT '',
  chunks_json TEXT DEFAULT '[]',
  FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE TABLE IF NOT EXISTS cards (
  product_id INTEGER PRIMARY KEY,
  card_json TEXT NOT NULL,
  pipeline_meta TEXT DEFAULT '{}',
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT DEFAULT '', name TEXT DEFAULT '', note TEXT DEFAULT '',
  status TEXT DEFAULT 'new',          -- new | done | skipped
  created_at TEXT, product_id INTEGER
);
CREATE TABLE IF NOT EXISTS views (
  day TEXT NOT NULL, path TEXT NOT NULL, n INTEGER DEFAULT 0,
  PRIMARY KEY (day, path)
);
CREATE TABLE IF NOT EXISTS traffic_daily (
  day TEXT NOT NULL, path TEXT NOT NULL, source TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, path, source)
);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER, action TEXT, detail TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS build_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER, image_urls TEXT, name TEXT, url TEXT,
  status TEXT DEFAULT 'new',   -- new | running | done | error
  created_at TEXT, finished_at TEXT, note TEXT
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ---------- products ----------
def create_product(name: str, maker: str = "", model: str = "", category: str = "",
                   is_sample: bool = False) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO products(name,maker,model,category,is_sample,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (name, maker, model, category, int(is_sample), "draft", now(), now()))
        return cur.lastrowid


def get_product(pid: int) -> dict[str, Any] | None:
    with connect() as con:
        r = con.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def list_products(status: str | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM products"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    q += " ORDER BY updated_at DESC"
    with connect() as con:
        return [dict(r) for r in con.execute(q, args).fetchall()]


def set_product_status(pid: int, status: str, note: str = "") -> None:
    with connect() as con:
        approved_at = now() if status == "approved" else None
        con.execute("UPDATE products SET status=?, reviewer_note=?, updated_at=?, "
                    "approved_at=COALESCE(?, approved_at) WHERE id=?",
                    (status, note, now(), approved_at, pid))
        con.execute("INSERT INTO review_log(product_id,action,detail,at) VALUES(?,?,?,?)",
                    (pid, status, note, now()))


def delete_product(pid: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM documents WHERE product_id=?", (pid,))
        con.execute("DELETE FROM cards WHERE product_id=?", (pid,))
        con.execute("DELETE FROM review_log WHERE product_id=?", (pid,))
        con.execute("DELETE FROM products WHERE id=?", (pid,))


# ---------- documents ----------
def add_document(product_id: int, kind: str, title: str, url: str, text: str,
                 chunks: list[str]) -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO documents(product_id,kind,title,url,fetched_at,text,chunks_json)"
            " VALUES(?,?,?,?,?,?,?)",
            (product_id, kind, title, url, now(), text, json.dumps(chunks, ensure_ascii=False)))
        return cur.lastrowid


def update_document_chunks(doc_id: int, chunks: list[str]) -> None:
    with connect() as con:
        con.execute("UPDATE documents SET chunks_json=? WHERE id=?", (json.dumps(chunks, ensure_ascii=False), doc_id))


def list_documents(product_id: int) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM documents WHERE product_id=? ORDER BY id",
                           (product_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["chunks"] = json.loads(d.pop("chunks_json") or "[]")
        out.append(d)
    return out


# ---------- cards ----------
def save_card(product_id: int, card: dict[str, Any], meta: dict[str, Any] | None = None) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO cards(product_id,card_json,pipeline_meta,updated_at) VALUES(?,?,?,?)"
            " ON CONFLICT(product_id) DO UPDATE SET card_json=excluded.card_json,"
            " pipeline_meta=excluded.pipeline_meta, updated_at=excluded.updated_at",
            (product_id, json.dumps(card, ensure_ascii=False),
             json.dumps(meta or {}, ensure_ascii=False), now()))
        con.execute("UPDATE products SET updated_at=? WHERE id=?", (now(), product_id))


def get_card(product_id: int) -> dict[str, Any] | None:
    with connect() as con:
        r = con.execute("SELECT card_json FROM cards WHERE product_id=?", (product_id,)).fetchone()
        return json.loads(r["card_json"]) if r else None


def get_card_meta(product_id: int) -> dict[str, Any]:
    with connect() as con:
        r = con.execute("SELECT pipeline_meta FROM cards WHERE product_id=?", (product_id,)).fetchone()
        return json.loads(r["pipeline_meta"]) if r else {}


# ---------- 정식 카드 요청 ----------
def add_request(url: str, name: str, note: str = "") -> int:
    with connect() as con:
        cur = con.execute("INSERT INTO requests(url,name,note,status,created_at) VALUES(?,?,?,?,?)",
                          (url[:500], name[:120], note[:500], "new", now()))
        return int(cur.lastrowid)


def list_requests(status: str | None = None) -> list[dict[str, Any]]:
    with connect() as con:
        if status:
            rows = con.execute("SELECT * FROM requests WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def set_request_status(rid: int, status: str, product_id: int | None = None) -> None:
    with connect() as con:
        con.execute("UPDATE requests SET status=?, product_id=COALESCE(?, product_id) WHERE id=?", (status, product_id, rid))


def count_requests(status: str = "new") -> int:
    with connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM requests WHERE status=?", (status,)).fetchone()[0])


# ---------- 페이지 조회 집계(개별 방문 기록 없이 일별 합계만 저장) ----------
def count_view(path: str, source: str = "direct") -> None:
    from .traffic import public_path, SOURCE_LABELS, today
    path = public_path(path)
    if path is None:
        return
    source = source if source in SOURCE_LABELS else "other"
    with connect() as con:
        con.execute("INSERT INTO traffic_daily(day,path,source,n) VALUES(?,?,?,1) "
                    "ON CONFLICT(day,path,source) DO UPDATE SET n=n+1", (today(), path, source))


def traffic_stats() -> dict[str, Any]:
    from datetime import date, timedelta
    from .traffic import today, PAGE_LABELS, SOURCE_LABELS
    end = today()
    start = (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    week = (date.fromisoformat(end) - timedelta(days=6)).isoformat()
    with connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT day,path,source,n FROM traffic_daily WHERE day BETWEEN ? AND ?", (start, end))]
        first = con.execute("SELECT MIN(day) FROM traffic_daily").fetchone()[0]
    paths, sources, daily = {}, {}, {}
    for row in rows:
        paths[row["path"]] = paths.get(row["path"], 0) + row["n"]
        sources[row["source"]] = sources.get(row["source"], 0) + row["n"]
        daily[row["day"]] = daily.get(row["day"], 0) + row["n"]
    return {
        "start": start, "end": end, "first": first,
        "total": sum(row["n"] for row in rows),
        "week": sum(row["n"] for row in rows if row["day"] >= week),
        "today": daily.get(end, 0),
        "paths": [{"path": p, "label": PAGE_LABELS.get(p, "제품 카드 " + p.rsplit("/", 1)[-1]), "n": n}
                  for p, n in sorted(paths.items(), key=lambda item: (-item[1], item[0]))],
        "sources": [{"label": SOURCE_LABELS.get(s, "기타 외부"), "n": n}
                    for s, n in sorted(sources.items(), key=lambda item: (-item[1], item[0]))],
        "days": [{"day": day, "n": daily.get(day, 0)} for day in
                 [(date.fromisoformat(end) - timedelta(days=i)).isoformat() for i in range(14)]],
    }


def view_stats(days: int = 30) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT path, SUM(n) AS n FROM views WHERE day >= date('now', ?) GROUP BY path ORDER BY n DESC",
                           (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]


# ---------- build queue (카드 생성 대기열: 서버가 재시작해도 남는다) ----------
def enqueue_build(pid: int, image_urls: list[str], name: str, url: str) -> int:
    with connect() as con:
        cur = con.execute("INSERT INTO build_queue(product_id,image_urls,name,url,status,created_at) VALUES(?,?,?,?,'new',?)",
                          (pid, json.dumps(image_urls), name, url, now()))
        return int(cur.lastrowid)


def next_build() -> dict[str, Any] | None:
    with connect() as con:
        con.execute("UPDATE build_queue SET status='new' WHERE status='running'")  # 재시작으로 끊긴 것은 다시
        row = con.execute("SELECT * FROM build_queue WHERE status='new' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return None
        con.execute("UPDATE build_queue SET status='running' WHERE id=?", (row["id"],))
        d = dict(row)
        d["image_urls"] = json.loads(d.get("image_urls") or "[]")
        return d


def finish_build(qid: int, ok: bool, note: str = "") -> None:
    with connect() as con:
        con.execute("UPDATE build_queue SET status=?, finished_at=?, note=? WHERE id=?",
                    ("done" if ok else "error", now(), note[:300], qid))


def queue_counts() -> dict[str, int]:
    with connect() as con:
        return {r["status"]: int(r["n"]) for r in con.execute("SELECT status, COUNT(*) n FROM build_queue GROUP BY status")}
