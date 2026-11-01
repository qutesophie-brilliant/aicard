"""Look up reviewed products without treating similar retailer copy as identity."""
import re
import unicodedata


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def candidates(query, products, *, partial=True):
    query = normalized(query)[:500]
    if len(query) < 2:
        return []
    ranked = []
    for product in products:
        if product.get('status') != 'approved' or product.get('is_sample'):
            continue
        name, model, maker = (normalized(product.get(k)) for k in ('name', 'model', 'maker'))
        exact = query in {v for v in (name, model, f'{maker} {name}', f'{maker} {model}') if v}
        # Keep model suffixes and punctuation: FX24 and FX24-PLUS are not
        # interchangeable. Never search recommendations or arbitrary OCR text.
        model_hit = bool(model and len(model) >= 4 and re.search(r'[a-z]', model)
                         and re.search(r'\d', model)
                         and re.search(r'(?<![\w-])' + re.escape(model) + r'(?![\w-])', query))
        name_hit = bool(len(name) >= 6 and name in query)
        loose = partial and all(token in f'{maker} {name} {model}' for token in query.split())
        if exact or model_hit or name_hit or loose:
            ranked.append((0 if exact else 1 if model_hit else 2 if name_hit else 3, product, exact))
    ranked.sort(key=lambda item: (item[0], item[1].get('name', ''), item[1]['id']))
    return [{'product': p, 'exact': exact} for _, p, exact in ranked]


def exact_product(matches):
    exact = [m['product'] for m in matches if m['exact']]
    return exact[0] if len(exact) == 1 else None
