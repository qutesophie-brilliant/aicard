"""Match recorded product URLs conservatively, retaining product identifiers."""
from urllib.parse import parse_qsl, urlsplit

_TRACKING = {'gclid', 'dclid', 'fbclid', 'msclkid', 'yclid', '_ga', '_gl',
             'mc_cid', 'mc_eid'}


def source_identity(url):
    try:
        parsed = urlsplit((url or '').strip())
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username:
            return None
        host = parsed.hostname.lower().removeprefix('www.')
        port = parsed.port
        if port and port not in (80, 443):
            host += ':' + str(port)
        path = parsed.path.rstrip('/') or '/'
        # Keep all unknown parameters, including goodsNo/modelId/itemId and
        # vendorItemId. A false negative is preferable to another product's card.
        query = tuple(sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                             if not key.lower().startswith('utm_') and key.lower() not in _TRACKING))
        if path == '/' and not query:
            return None
        return host, path, query
    except (TypeError, ValueError):
        return None
