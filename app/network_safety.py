"""Bounded public-web fetches. Validate and pin every connection and redirect."""
import ipaddress
import socket

import httpx


class PublicURLRejected(ValueError):
    pass


def public_addresses(url: str) -> tuple[httpx.URL, list[str]]:
    try:
        parsed = httpx.URL(url)
        host = parsed.host.rstrip('.')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except (ValueError, httpx.InvalidURL) as exc:
        raise PublicURLRejected('올바른 웹페이지 주소를 입력해 주세요.') from exc
    if parsed.scheme not in ('http', 'https') or not host or parsed.userinfo:
        raise PublicURLRejected('로그인 정보가 포함되지 않은 공개 웹페이지 주소를 입력해 주세요.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '%' in host:
        raise PublicURLRejected('내부망 주소는 분석할 수 없습니다. 공개된 제품 페이지 주소를 입력해 주세요.')
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
                host, port, type=socket.SOCK_STREAM)))
        except OSError as exc:
            raise PublicURLRejected('주소를 찾지 못했습니다. 제품 페이지 주소를 확인해 주세요.') from exc
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise PublicURLRejected('내부망 주소는 분석할 수 없습니다. 공개된 제품 페이지 주소를 입력해 주세요.')
    return parsed.copy_with(host=host, fragment=None), addresses


def _public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip.is_global and not ip.is_multicast
    except ValueError:
        return False


def fetch_public(url: str, *, timeout: float = 25, max_bytes: int = 16 * 1024 * 1024,
                 max_redirects: int = 5, headers: dict | None = None,
                 allowed_hosts: set[str] | None = None, image_only: bool = False,
                 https_only: bool = False) -> httpx.Response:
    current = url
    for hop in range(max_redirects + 1):
        original, addresses = public_addresses(current)
        if https_only and original.scheme != 'https':
            raise PublicURLRejected('안전한 이미지 주소로 연결되지 않았습니다.')
        if allowed_hosts is not None and original.host not in allowed_hosts:
            raise PublicURLRejected('허용되지 않은 이미지 주소입니다.')
        response = None
        for index, address in enumerate(addresses):
            # Connect to the validated numeric address, retaining the original Host
            # and TLS certificate name. The HTTP library cannot resolve the name a
            # second time and accidentally connect to a rebound private address.
            pinned = original.copy_with(host=address)
            request_headers = {**(headers or {}), 'Host': original.netloc.decode('ascii')}
            try:
                with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                    with client.stream('GET', pinned, headers=request_headers,
                                       extensions={'sni_hostname': original.host}) as stream:
                        data = bytearray()
                        if not stream.is_redirect:
                            stream.raise_for_status()
                            if image_only and not stream.headers.get('content-type', '').startswith('image/'):
                                raise PublicURLRejected('이미지 파일이 아닌 응답입니다.')
                            for block in stream.iter_bytes():
                                if len(data) + len(block) > max_bytes:
                                    raise PublicURLRejected('분석 가능한 다운로드 크기를 초과했습니다.')
                                data.extend(block)
                        response_headers = {k: v for k, v in stream.headers.items()
                                            if k not in ('content-encoding', 'content-length')}
                        response = httpx.Response(stream.status_code, headers=response_headers,
                                                  content=bytes(data), request=httpx.Request('GET', original))
                break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise
        if response is None:
            raise PublicURLRejected('페이지에 연결하지 못했습니다.')
        if not response.is_redirect:
            return response
        location = response.headers.get('location')
        if not location or hop == max_redirects:
            raise PublicURLRejected('페이지의 주소 이동을 확인하지 못했습니다.')
        current = str(original.join(location))
    raise PublicURLRejected('페이지의 주소 이동이 너무 많습니다.')
