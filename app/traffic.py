"""Aggregate page views only. No visitor identifiers or raw request data are stored."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

KST = timezone(timedelta(hours=9))
PAGE_LABELS = {
    "/": "홈", "/about": "서비스 소개", "/check": "바로 확인",
    "/compare": "제품 비교", "/findings": "공개자료 분석",
}
SOURCE_LABELS = {
    "direct": "직접 방문 · 출처 미제공", "internal": "사이트 내 이동",
    "google": "Google", "naver": "네이버", "bing": "Bing", "daum": "다음",
    "youtube": "YouTube", "kakao": "카카오", "github": "GitHub", "other": "기타 외부",
}
SOURCE_DOMAINS = {
    "google": ("google.com", "google.co.kr"), "naver": ("naver.com",),
    "bing": ("bing.com",), "daum": ("daum.net",),
    "youtube": ("youtube.com", "youtu.be"), "kakao": ("kakao.com", "kakao.co.kr"),
    "github": ("github.com", "github.io"),
}
BOT_PATTERN = re.compile(r"bot|crawler|spider|slurp|headless|lighthouse|pagespeed|uptime|monitor|curl|wget|python|httpx|testclient", re.I)


def today() -> str:
    return datetime.now(KST).date().isoformat()


def public_path(path: str) -> str | None:
    """Allowlist public pages, dropping all query strings and fragments."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    if path in PAGE_LABELS:
        return path
    if re.fullmatch(r"/card/[0-9]{1,9}", path):
        return path
    return None


def source_bucket(referrer: str, site_host: str) -> str:
    if not referrer:
        return "direct"
    try:
        url = urlsplit(referrer)
        host = (url.hostname or "").lower().rstrip(".")
    except ValueError:
        return "other"
    if url.scheme not in {"http", "https"} or not host:
        return "other"
    if host in {site_host.lower().rstrip("."), "ai-infocard.co.kr", "www.ai-infocard.co.kr"}:
        return "internal"
    for source, domains in SOURCE_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return source
    return "other"


def request_view(request, response, *, enabled: bool, reviewer_token: str = "") -> tuple[str, str] | None:
    if not enabled or request.method != "GET" or response.status_code != 200:
        return None
    if not response.headers.get("content-type", "").startswith("text/html"):
        return None
    path = public_path(request.url.path)
    if not path or "preview" in request.query_params or "token" in request.query_params:
        return None
    if reviewer_token and request.cookies.get("aicard_token") == reviewer_token:
        return None
    headers = request.headers
    if headers.get("dnt") == "1" or headers.get("sec-gpc") == "1":
        return None
    ua = headers.get("user-agent", "")
    if not ua or BOT_PATTERN.search(ua):
        return None
    purpose = headers.get("purpose", "") + headers.get("sec-purpose", "")
    if "prefetch" in purpose.lower():
        return None
    if headers.get("sec-fetch-mode", "navigate") != "navigate":
        return None
    if headers.get("sec-fetch-dest", "document") != "document":
        return None
    return path, source_bucket(headers.get("referer", ""), request.url.hostname or "")
