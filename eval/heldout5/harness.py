"""시험 세트(heldout5) 로컬 분석 도구.
서버 export(문서 원문·카드)를 다시 청킹해 후보 생성 단계를 재현하고,
라벨러가 적은 근거 문장이 후보에 들어왔는지, 규칙 후처리가 어디서 막는지 본다. LLM은 부르지 않는다."""
import csv, json, re, sys
sys.path.insert(0, ".")
from app.pipeline import ingest, link, llm
from app.pipeline.retrieve import ChunkIndex

EXP = "eval/heldout5/exports/exports_full_1011.json"
GOLD = "eval/heldout5/gold_majority.csv"


def load():
    d = json.load(open(EXP, encoding="utf-8"))
    gold = {(r["pid"], r["claim_id"]): r for r in csv.DictReader(open(GOLD, encoding="utf-8-sig"))}
    return d, gold


def docs_of(exp):
    docs = []
    for doc in exp["documents"]:
        ch = doc.get("chunks") or ingest.chunk_text(doc["text"] or "", ocr=(doc.get("title") or "").startswith("상세 이미지"))
        docs.append({"id": doc["id"], "kind": doc["kind"], "title": doc.get("title"), "url": doc.get("url"), "chunks": ch})
    return docs


def locate(index, quote):
    """라벨러 인용 앞 30자를 담은 청크를 찾는다(공백 무시)."""
    q = re.sub(r"\s", "", quote)[:24]
    if not q:
        return None
    for d, i, t, k in index.entries:
        if q in re.sub(r"\s", "", t):
            return {"doc_id": d, "chunk": i, "quote": t, "kind": k}
    qb = {q[i:i+2] for i in range(len(q)-1)}
    best, bs = None, 0
    for d, i, t, k in index.entries:
        tt = re.sub(r"\s", "", t); tb = {tt[i:i+2] for i in range(len(tt)-1)}
        sc = len(qb & tb) / max(1, len(qb))
        if sc > bs: bs, best = sc, {"doc_id": d, "chunk": i, "quote": t, "kind": k}
    return best if bs >= 0.6 else None


def claims_of(exp, index):
    """카드의 문구를 새 청킹에 다시 매핑한다(source chunk를 다시 찾는다)."""
    out = []
    for c in exp["card"]["claims"]:
        src = {"doc_id": c["source"]["doc_id"], "chunk": c["source"]["chunk"], "quote": c["source"]["quote"], "kind": "판매페이지"}
        out.append({"id": c["id"], "text": c["text"], "item": c.get("item"), "label": c.get("label"),
                    "source": {"doc_id": src["doc_id"], "chunk": src["chunk"], "quote": src["quote"]}})
    return out


def candidates(claims, index):
    """link_claims의 후보 생성만 실행한다(LLM 없이). cand_map을 돌려준다."""
    with llm.rules_only():
        saved = link._link_rules
        cand_map = {}
        def spy(claim, cands):
            cand_map[claim["id"]] = list(cands)
            saved(claim, cands)
        link._link_rules = spy
        try:
            link.link_claims([dict(c) for c in claims], index)
        finally:
            link._link_rules = saved
    return cand_map


def simulate(claim, cands, ev_cands, status="근거 확인"):
    """LLM이 ev_cands를 근거로 status라고 답했다고 가정하고 규칙 후처리(_apply)를 돌린다."""
    row = {"status": status, "evidence": [cands.index(e) for e in ev_cands], "conditions": "", "basis": "sensor", "note": ""}
    c = dict(claim)
    c["_cond_pool"] = [t for t in link.ChunkIndex.by_kind(INDEX, ("판매페이지", "사양표", "설명서", "공식 자료")) if link._is_cond(t["quote"])]
    link._apply(c, cands, row)
    return c


INDEX = None

if __name__ == "__main__":
    d, gold = load()
    only = sys.argv[1:]
    for pid, exp in d.items():
        docs = docs_of(exp)
        INDEX = index = ChunkIndex(docs)
        claims = claims_of(exp, index)
        cmap = candidates(claims, index)
        for cl in claims:
            g = gold[(pid, cl["id"])]
            key = f"{pid}:{cl['id']}"
            if only and key not in only:
                continue
            if g["decision"] != "include":
                continue
            cands = cmap[cl["id"]]
            gq = g["gold_evidence_quote"]
            hit = locate(index, gq) if gq else None
            hq = re.sub(r"\s", "", hit["quote"])[:20] if hit else ""
            in_c = any((c["doc_id"], c["chunk"]) == (hit["doc_id"], hit["chunk"]) or (hq and hq in re.sub(r"\s", "", c["quote"])) for c in cands) if hit else None
            print(f"\n=== {key} gold={g['basis']}/{g['conditions_noted']}  quote_in_doc={'Y' if hit else '-'} in_cands={in_c}")
            print("  문구:", cl["text"][:80])
            if hit:
                print("  gold청크:", hit["kind"], hit["quote"][:110].replace("\n", " "))
                if in_c:
                    ec = next(c for c in cands if (c["doc_id"], c["chunk"]) == (hit["doc_id"], hit["chunk"]) or (hq and hq in re.sub(r"\s", "", c["quote"])))
                    sim = simulate(cl, cands, [ec])
                    print("  규칙후처리(LLM=근거확인 가정):", sim["status"], "|", (sim.get("note") or "")[:90])
            if "-v" in sys.argv:
                for i, c in enumerate(cands):
                    print(f"   [{i}] {c['kind']} {c['quote'][:70]}")
