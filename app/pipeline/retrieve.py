"""근거 후보 검색. 한국어는 형태소 분석기 없이 문자 2-gram 으로 BM25 를 돌린다."""
import re
from typing import Any

from rank_bm25 import BM25Okapi

_token_re = re.compile(r"[가-힣]+|[a-zA-Z]+|\d+(?:\.\d+)?")


def tokenize(text: str) -> list[str]:
    toks: list[str] = []
    for w in _token_re.findall(text.lower()):
        if re.fullmatch(r"[가-힣]+", w):
            if len(w) == 1:
                toks.append(w)
            else:
                toks.extend(w[i:i + 2] for i in range(len(w) - 1))
                if len(w) >= 3:
                    toks.append(w)
        else:
            toks.append(w)
    return toks


class ChunkIndex:
    """여러 문서의 청크를 하나의 색인으로 묶는다. 각 항목은 (doc_id, chunk_idx, text)."""

    def __init__(self, docs: list[dict[str, Any]]):
        self.entries: list[tuple[int, int, str, str]] = []
        for d in docs:
            for i, c in enumerate(d["chunks"]):
                self.entries.append((d["id"], i, c, d["kind"]))
        corpus = [tokenize(e[2]) for e in self.entries] or [["_"]]
        self.bm25 = BM25Okapi(corpus)

    def search(self, query: str, k: int = 6, exclude: tuple[int, int] | None = None,
               prefer_kinds: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        if not self.entries:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in ranked:
            doc_id, idx, text, kind = self.entries[i]
            if exclude and (doc_id, idx) == exclude:
                continue
            if scores[i] <= 0:
                break
            bonus = 0.15 if kind in prefer_kinds else 0.0
            out.append({"doc_id": doc_id, "chunk": idx, "quote": text, "kind": kind,
                        "score": float(scores[i]) * (1 + bonus)})
            if len(out) >= k * 2:
                break
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:k]

    def by_kind(self, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
        return [{"doc_id": d, "chunk": i, "quote": t, "kind": k}
                for d, i, t, k in self.entries if k in kinds]
