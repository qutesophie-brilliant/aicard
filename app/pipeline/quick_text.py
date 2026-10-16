"""Sentence boundaries for the unsaved quick preview only.

The formal pipeline and its historical evaluation inputs remain unchanged.
"""
import re
from .ingest import normalize_spacing


def chunks(text):
    text = normalize_spacing(text)
    # Do not split after the syllables 요/다/함: they also occur in 필요, 주요,
    # and other words inside a sentence. Never hard-cut a displayed quotation.
    parts = re.split(r'(?<=[.!?。])\s+(?=\S)|\n+', text)
    return [re.sub(r'[ \t　]+', ' ', p).strip() for p in parts if len(p.strip()) >= 6]
