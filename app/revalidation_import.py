"""Authenticated proposal upload: stage source-backed remedy edits, never approve."""
import copy
import json
import threading
from . import db, revalidation, revalidation_batch
from .draft_precheck import digest

_lock = threading.Lock()


def stage(package):
    if not isinstance(package,dict) or set(package) != {'version','title','rows','held'} or package['version'] != 1:
        raise ValueError('검토안 파일의 버전을 확인해 주세요.')
    rows = package['rows']
    if not isinstance(rows,list) or not 1 <= len(rows) <= 100 or not isinstance(package['held'],list):
        raise ValueError('검토안은 1~100장으로 구성해야 합니다.')
    required = {'product_id','expected_card_digest','card','source','summary','changes','checks'}
    ids = [r.get('product_id') for r in rows if isinstance(r,dict)]
    if len(ids) != len(rows) or len(set(ids)) != len(ids):
        raise ValueError('카드 번호가 중복되거나 누락됐습니다.')
    key = digest(package)
    with _lock:
        with db.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS revalidation_imports (digest TEXT PRIMARY KEY,batch_id TEXT NOT NULL)')
            prior = con.execute('SELECT batch_id FROM revalidation_imports WHERE digest=?',(key,)).fetchone()
        if prior:
            return {'url':f'/review/revalidation-batch/{prior["batch_id"]}','reused':True,'applied':False}
        payloads=[]
        for row in rows:
            if set(row) != required or type(row['product_id']) is not int:
                raise ValueError('카드별 검토안 형식을 확인해 주세요.')
            with db.connect() as con:
                p,c,docs = revalidation.snapshot(con,row['product_id'])
            before = json.loads(c['card_json'])
            if digest(before) != row['expected_card_digest']:
                raise ValueError(f'카드 {row["product_id"]}의 내용이 변경됐습니다. 다시 대조해 주세요.')
            payload = {k:copy.deepcopy(v) for k,v in row.items() if k != 'expected_card_digest'}
            payload.update(expected_fingerprint=revalidation.fingerprint(p,c,docs),review_scope='remedy')
            payloads.append(payload)
        # These create pending review records only. Atomic publication remains
        # exclusively behind the existing explicit batch approval form.
        plans = [revalidation.create(p)['id'] for p in payloads]
        result = revalidation_batch.create({'title':package['title'],'ready':plans,'held':package['held']})
        with db.connect() as con:
            con.execute('INSERT INTO revalidation_imports VALUES(?,?)',(key,result['id']))
        return result
