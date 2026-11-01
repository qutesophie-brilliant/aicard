"""Match recorded product URLs conservatively, retaining product identifiers."""
import re
from urllib.parse import parse_qsl, urlsplit

_TRACKING = {'gclid', 'dclid', 'fbclid', 'msclkid', 'yclid', '_ga', '_gl',
             'mc_cid', 'mc_eid', 'srsltid', 'gbraid', 'wbraid'}


def _path_encoding(path):
    # Decode only unreserved ASCII. %2F must remain an encoded slash, and path
    # case, model suffixes and default-document names retain their identity.
    def replace(match):
        char = chr(int(match.group(1), 16))
        return char if re.fullmatch(r'[A-Za-z0-9._~-]', char) else '%' + match.group(1).upper()
    return re.sub(r'%([0-9a-fA-F]{2})', replace, path)


def source_identity(url):
    try:
        parsed = urlsplit((url or '').strip())
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username:
            return None
        host = parsed.hostname.lower().removeprefix('www.')
        port = parsed.port
        if port and port not in (80, 443):
            host += ':' + str(port)
        path = _path_encoding(parsed.path).rstrip('/') or '/'
        # Keep all unknown parameters, including goodsNo/modelId/itemId and
        # vendorItemId. A false negative is preferable to another product's card.
        query = tuple(sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                             if not key.lower().startswith('utm_') and key.lower() not in _TRACKING))
        if path == '/' and not query:
            return None
        return host, path, query
    except (TypeError, ValueError):
        return None
