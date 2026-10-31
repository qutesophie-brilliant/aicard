"""Sentence boundaries for the unsaved quick preview only.

The formal pipeline and its historical evaluation inputs remain unchanged.
"""
import re
from .ingest import normalize_spacing


def chunks(text):
    text = normalize_spacing(text)
    # PDF/OCR visual line breaks can split one sentence after a connective.
    # Join only explicit continuations; keep headings and footnotes separate.
    lines=[]
    continuation=re.compile(r'(?:감지해|감지하여|분석해|분석하여|학습해|학습하여|통해|따라|조합해|조합하여|조절해|조절하여|인식해|인식하여|조절하고|분석하고|인식하고|감지하고|맞춤\s*운전으로)$')
    for line in text.splitlines():
        line=line.strip()
        if not line:
            lines.append('')
        elif lines and continuation.search(lines[-1]) and not re.match(r'^[*※†①-⑳]|^\d{1,2}[).]',line):
            lines[-1]+=' '+line
        else:
            lines.append(line)
    text='\n'.join(lines)
    # Do not split after the syllables 요/다/함: they also occur in 필요, 주요,
    # and other words inside a sentence. Never hard-cut a displayed quotation.
    parts = re.split(r'(?<=[.!?。])\s+(?=\S)|\n+', text)
    return [re.sub(r'[ \t　]+', ' ', p).strip() for p in parts if len(p.strip()) >= 6]
