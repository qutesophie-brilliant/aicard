import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from app import db, main, traffic

BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36"


def request(path="/about", method="GET", headers=None, query=""):
    return Request({"type": "http", "scheme": "https", "method": method, "path": path,
                    "query_string": query.encode(), "server": ("ai-infocard.co.kr", 443),
                    "headers": [(k.lower().encode(), v.encode()) for k, v in
                                {"host": "ai-infocard.co.kr", "user-agent": BROWSER, **(headers or {})}.items()]})


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dbpatch = patch.object(db, "DB_PATH", Path(self.tmp.name) / "traffic.sqlite3")
        self.dbpatch.start()
        self.addCleanup(self.dbpatch.stop)
        db.init_db()

    def test_referrers_keep_only_known_categories(self):
        cases = {
            "": "direct", "https://www.google.com/search?q=private": "google",
            "https://search.naver.com/?query=private": "naver",
            "https://www.google.co.kr/?q=private": "google",
            "https://www.google.com.evil.test": "other",
            "https://notgoogle.com": "other", "https://github.com/user/private": "github",
            "https://ai-infocard.co.kr/card/67?query=private": "internal",
            "https://www.ai-infocard.co.kr/": "internal", "https://external.example/private": "other",
            "invalid": "other", "https://[invalid": "other", "javascript:private": "other",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(traffic.source_bucket(url, "ai-infocard.co.kr"), expected)

    def test_only_successful_public_html_navigation_counts(self):
        self.assertEqual(traffic.request_view(request(query="q=private"), HTMLResponse("ok"), enabled=True), ("/about", "direct"))
        cases = [
            (request("/about/missing"), HTMLResponse("no")),
            (request("/review"), HTMLResponse("no")),
            (request("/api/cards.json"), JSONResponse({})),
            (request("/check/receive"), HTMLResponse("no")),
            (request("/card/12/doc/1"), HTMLResponse("no")),
            (request(), HTMLResponse("no", status_code=404)),
            (request(), HTMLResponse("no", status_code=500)),
            (request(method="HEAD"), HTMLResponse("no")),
            (request(method="POST"), HTMLResponse("no")),
            (request(), JSONResponse({})),
        ]
        for req, resp in cases:
            self.assertIsNone(traffic.request_view(req, resp, enabled=True))
        self.assertIsNone(traffic.request_view(request(), HTMLResponse("no"), enabled=False))

    def test_private_and_automated_requests_are_excluded(self):
        cases = [
            request(headers={"user-agent": "Googlebot"}), request(headers={"user-agent": "python-httpx/1"}),
            request(headers={"user-agent": ""}), request(headers={"DNT": "1"}),
            request(headers={"Sec-GPC": "1"}), request(headers={"Sec-Purpose": "prefetch;prerender"}),
            request(headers={"Sec-Fetch-Mode": "cors"}), request(headers={"Sec-Fetch-Dest": "iframe"}),
            request(query="preview=1"), request(query="token=private"),
            request(headers={"cookie": "aicard_token=reviewer-test"}),
        ]
        for req in cases:
            self.assertIsNone(traffic.request_view(req, HTMLResponse("ok"), enabled=True, reviewer_token="reviewer-test"))

    def test_storage_is_aggregate_and_drops_raw_values(self):
        db.count_view("/card/67?q=PRIVATE#PRIVATE", "google")
        db.count_view("/card/67?q=DIFFERENT", "google")
        db.count_view("/about", "https://private.example/name")
        db.count_view("/review/private")
        with db.connect() as con:
            columns = [r[1] for r in con.execute("PRAGMA table_info(traffic_daily)")]
            rows = [dict(r) for r in con.execute("SELECT * FROM traffic_daily")]
        self.assertEqual(columns, ["day", "path", "source", "n"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(r for r in rows if r["path"] == "/card/67")["n"], 2)
        self.assertNotIn("PRIVATE", str(rows))
        self.assertNotIn("private.example", str(rows))

    def test_date_windows_are_inclusive_and_legacy_totals_stay_separate(self):
        with db.connect() as con:
            con.execute("INSERT INTO views VALUES('2026-10-31','/',999)")
            con.executemany("INSERT INTO traffic_daily VALUES(?,?,?,?)", [
                ("2026-10-31", "/", "direct", 2), ("2026-10-25", "/about", "google", 3),
                ("2026-10-24", "/about", "naver", 4), ("2026-10-02", "/card/67", "internal", 5),
                ("2026-10-01", "/", "direct", 50), ("2026-11-01", "/", "direct", 500),
            ])
        with patch.object(traffic, "today", return_value="2026-10-31"):
            stats = db.traffic_stats()
        self.assertEqual((stats["today"], stats["week"], stats["total"]), (2, 5, 14))
        self.assertEqual(stats["start"], "2026-10-02")
        self.assertEqual(len(stats["days"]), 14)
        self.assertEqual(sum(r["n"] for r in stats["paths"]), 14)
        self.assertEqual(sum(r["n"] for r in stats["sources"]), 14)
        with db.connect() as con:
            self.assertEqual(con.execute("SELECT n FROM views").fetchone()[0], 999)

    def test_middleware_and_dashboard_access(self):
        client = TestClient(main.app, headers={"user-agent": BROWSER})
        with patch.object(main.config, "TRAFFIC_ENABLED", True), patch.object(main.config, "REVIEW_TOKEN", "reviewer-test"):
            self.assertEqual(client.get("/about?q=PRIVATE", headers={"Referer": "https://www.google.com/?q=PRIVATE"}).status_code, 200)
            self.assertEqual(db.traffic_stats()["total"], 1)
            response = client.get("/review/traffic", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/review/login?next=/review/traffic")
            client.cookies.set("aicard_token", "reviewer-test")
            client.get("/about")
            response = client.get("/review/traffic")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("페이지 이용 통계", response.text)
            self.assertNotIn("PRIVATE", response.text)
            self.assertEqual(db.traffic_stats()["total"], 1)

    def test_counter_failure_does_not_break_page(self):
        with patch.object(main.config, "TRAFFIC_ENABLED", True), patch.object(db, "count_view", side_effect=RuntimeError("locked")):
            client = TestClient(main.app, headers={"user-agent": BROWSER})
            self.assertEqual(client.get("/about").status_code, 200)


if __name__ == "__main__":
    unittest.main()
