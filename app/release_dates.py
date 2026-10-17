"""Official model-matched release dates; separate from research/card data."""
import json
from pathlib import Path
_DATA = json.loads(Path(__file__).with_name("release_dates.json").read_text(encoding="utf-8"))
def release_for(product):
    item = _DATA.get(str(product.get("id")))
    if item and item["model"].casefold() == (product.get("model") or "").strip().casefold():
        return item
    return None
