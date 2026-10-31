"""Isolated, read-only rendering for JavaScript product shells.

All HTTP requests are fulfilled through the pinned public-IP fetcher. Chromium
has a dead proxy as a second barrier, no service workers, no WebSockets, no
credentials, no POSTs and no downloaded image/video/font payloads. Rendering
ends before OCR. The supervisor kills the process group on deadline or cgroup
memory pressure, protecting the serving process on small Render instances.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .product_page import ProductBodyUnavailable

MAX_SECONDS = 38
MAX_REQUESTS = 90
MAX_BYTES = 24 * 1024 * 1024


def memory_pressure():
    for usage, maximum in (
        ('/sys/fs/cgroup/memory.current', '/sys/fs/cgroup/memory.max'),
        ('/sys/fs/cgroup/memory/memory.usage_in_bytes', '/sys/fs/cgroup/memory/memory.limit_in_bytes')
    ):
        try:
            current = int(Path(usage).read_text())
            limit = int(Path(maximum).read_text())
            return current > limit * 0.86
        except (OSError, ValueError):
            continue
    return False


def render(url):
    from ..network_safety import public_addresses
    public_addresses(url)
    if os.getenv('CHECK_RENDER_FALLBACK', '1') != '1':
        raise ProductBodyUnavailable('동적으로 불러오는 상품 설명을 이번 분석에서 가져오지 못했습니다.')
    # The worker receives no API keys, database configuration or application secrets.
    env = {k: v for k, v in os.environ.items() if k in
           ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'PYTHONPATH',
            'PLAYWRIGHT_BROWSERS_PATH', 'LD_LIBRARY_PATH')}
    env['PYTHONIOENCODING'] = 'utf-8'
    with tempfile.TemporaryDirectory(prefix='aicard-render-') as temp:
        output = Path(temp) / 'snapshot.json'
        proc = subprocess.Popen([sys.executable, '-m', __name__, url, str(output)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env,
                                start_new_session=os.name != 'nt')
        reason = 'timeout'
        try:
            deadline = time.monotonic() + MAX_SECONDS
            while proc.poll() is None and time.monotonic() < deadline:
                if memory_pressure():
                    reason = 'memory'
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                raise ProductBodyUnavailable('상품 페이지를 자동으로 여는 중 서버 처리 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.' if reason == 'memory' else '상품 페이지의 상세정보가 제한 시간 안에 열리지 않았습니다.')
            if proc.returncode or not output.exists():
                raise ProductBodyUnavailable('상품 페이지의 자동 열기를 완료하지 못했습니다. 공개된 상세주소인지 확인해 주세요.')
            result = json.loads(output.read_text(encoding='utf-8'))
            if result.get('error'):
                logging.getLogger(__name__).warning('Product renderer failed: %s', result['error'])
                raise ProductBodyUnavailable('자동으로 페이지를 열었지만 상품 설명을 확인하지 못했습니다. 로그인이나 판매처의 접근 제한이 있는지 확인해 주세요.')
            return result
        finally:
            # Also reap browser children if the worker crashed or returned early.
            if os.name != 'nt':
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            elif proc.poll() is None:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.wait(timeout=5)


def run_browser(url, fetch=None):
    import asyncio
    from playwright.async_api import async_playwright
    from ..network_safety import fetch_public
    from .ingest import UA
    fetch = fetch or fetch_public

    async def work():
        count = 0
        blocked = 0
        total = 0
        deadline = time.monotonic() + 30
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True,
                proxy={'server': 'http://127.0.0.1:9'},
                args=['--proxy-bypass-list=<-loopback>', '--disable-background-networking',
                      '--disable-dev-shm-usage', '--disable-extensions',
                      '--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
            try:
                context = await browser.new_context(service_workers='block', accept_downloads=False,
                                                    user_agent=UA, viewport={'width': 1024, 'height': 768})
                await context.route_web_socket('**/*', lambda ws: ws.close())
                lock = asyncio.Semaphore(4)

                async def intercept(route):
                    nonlocal count, blocked, total
                    request = route.request
                    if (request.method != 'GET' or request.resource_type in ('image', 'media', 'font')
                        or count >= MAX_REQUESTS or total >= MAX_BYTES or time.monotonic() >= deadline):
                        blocked += 1
                        await route.abort()
                        return
                    count += 1
                    async with lock:
                        if time.monotonic() >= deadline or total >= MAX_BYTES:
                            blocked += 1
                            await route.abort()
                            return
                        try:
                            # Never route.continue_(): even redirects re-enter the pinned fetcher.
                            response = await asyncio.to_thread(fetch, request.url,
                                timeout=min(6, max(0.5, deadline-time.monotonic())),
                                max_bytes=min(4*1024*1024, MAX_BYTES-total),
                                headers={'User-Agent': UA, 'Accept': request.headers.get('accept', '*/*')},
                                follow_redirects=False)
                            if total + len(response.content) > MAX_BYTES:
                                raise ValueError('budget')
                            total += len(response.content)
                            headers = {k:v for k,v in response.headers.items() if k.lower() not in
                                       ('set-cookie','content-encoding','content-length','transfer-encoding','connection')}
                            await route.fulfill(status=response.status_code, headers=headers, body=response.content)
                        except Exception:
                            blocked += 1
                            await route.abort()

                await context.route('**/*', intercept)
                page = await context.new_page()
                context.on('page', lambda extra: asyncio.create_task(extra.close()) if extra != page else None)
                try:
                    await page.goto(url, wait_until='domcontentloaded', timeout=18000)
                except Exception:
                    pass  # Some shops never finish navigation; only actual product DOM can pass later.
                # Scroll to explicit descriptions; never click purchases/consent/login controls.
                from .product_page import DETAIL_SELECTOR
                for step in range(6):
                    if time.monotonic() >= deadline: break
                    await page.evaluate('''([selector, step]) => {
                        const nodes = Array.from(document.querySelectorAll(selector));
                        const target = nodes.find(n => n.tagName !== 'IFRAME');
                        if (target && step === 0) target.scrollIntoView();
                        else window.scrollBy(0, 700);
                    }''', [DETAIL_SELECTOR, step])
                    await page.wait_for_timeout(650)
                html = await page.content()
                # A rendered iframe has its own document: page.content() alone
                # would lose it. Only explicitly identified description frames
                # are captured, with their own source URL retained.
                from bs4 import BeautifulSoup
                from urllib.parse import urljoin
                from .product_page import image_source, _NOISE
                soup = BeautifulSoup(html, 'html.parser')
                for frame in page.frames[1:4]:
                    try:
                        owner = await frame.frame_element()
                        identified = await owner.evaluate('(el, selector) => !!el.closest(selector)', DETAIL_SELECTOR)
                        if not identified or frame.url.startswith(('about:', 'data:')): continue
                        if any(word in frame.url.lower() for word in ('youtube','vimeo','doubleclick','review','recommend','tracking','advert')): continue
                        from .page_recovery import access_problem
                        content = await frame.content()
                        if access_problem(content, frame.url): continue
                        child = BeautifulSoup(content, 'html.parser')
                        for node in list(child.select('script,style,nav,header,footer,iframe,object,embed')):
                            node.decompose()
                        for node in list(child.find_all(True)):
                            if node.parent is not None and _NOISE.search(' '.join([node.get('id',''),*node.get('class',[])])):
                                node.decompose()
                        for im in child.select('img'):
                            im['src'] = urljoin(frame.url, image_source(im))
                            for key in ('ec-data-src','data-src','data-original','data-lazy-src','data-echo','srcset','data-srcset'):
                                im.attrs.pop(key,None)
                        holder = soup.new_tag('div', attrs={'class':'product-description','data-source-url':frame.url})
                        for node in list((child.body or child).contents): holder.append(node)
                        target = next((n for n in soup.select(DETAIL_SELECTOR) if n.name != 'iframe'), soup.body or soup)
                        target.append(holder)
                        for old in list(soup.select('iframe')):
                            if urljoin(page.url,old.get('src','')) == frame.url: old.decompose()
                    except Exception:
                        continue
                html = str(soup)
                if len(html.encode('utf-8')) > 8*1024*1024:
                    raise ValueError('snapshot_limit')
                return {'url': page.url, 'html': html, 'requests': count, 'blocked_requests': blocked}
            finally:
                await browser.close()
    return asyncio.run(work())


if __name__ == '__main__':
    try:
        result = run_browser(sys.argv[1])
    except Exception as exc:
        result = {'error': type(exc).__name__}
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
