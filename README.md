# AI 정보카드 — ‘AI’ 표시, 근거까지 읽기

2026 AI 라이프 솔루션 챌린지 출품작. 아이디어 챌린지 수상 대상 후보작 「소비자 친화 AI 정보카드」를 제안자 본인이 구현했다.

**실서비스: https://aicard-3b2h.onrender.com**

제품 판매페이지의 ‘AI’ 표시 문구를 하나씩 꺼내 같은 회사가 공개한 각주·설명서·사양표와 공공 DB에서 작동 설명을 찾아 연결하고,
작동 설명 있음 / 없음 / 설명 대조 필요 세 가지 표시와 소비자 권익 7항목(다섯 질문)을 한 장의 카드로 보여 준다.
우열·진위·법 위반은 판단하지 않으며, 검토자가 원문을 확인해 승인한 카드만 공개된다.

## 검증 상태 (2026-10-13 정정)

- 현재 카드·문구 수는 서비스의 전체 통계에서 확인한다. 오래된 고정 수치를 재사용하지 않는다.
- 71.4%는 2026-10-12 저장된 56개 문구와 기준 라벨의 비교 기록이다. 현재 수정본 정확도로 제시하지 않는다.
- 라벨 작성자의 독립성과 실제 참여 기록은 별도 검증이 필요하다.
- 기존 6명 사용성 수치는 가상 검토 자료에서 나온 값이다. 실제 소비자 측정 성과라는 주장을 철회한다. 원본 파일은 이력을 위해 보존한다.
- 화면 검수, 기능 회귀검사, 독립된 정확도 평가, 실제 사용성 시험을 구분한다.

## 5분 안에 실행하기

**Windows:** `run_windows.bat`를 두 번 클릭. 처음 실행하면 `.env`가 메모장으로 열리니 `GEMINI_API_KEY=` 뒤에 키를 넣고 저장한 뒤 다시 두 번 클릭.

**Mac·Linux:**

```bash
cd aicard
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # 키를 넣으면 LLM 판정, 없으면 규칙 기반 데모
python seed.py                                         # 교육용 가상 제품 2건 등록(승인 상태)
uvicorn app.main:app --reload --port 8000
```

브라우저에서 http://127.0.0.1:8000

| 화면 | 주소 | 누가 |
|---|---|---|
| 첫 화면(최근 카드 + 갤러리) | `/` | 투표자·심사위원 |
| 카드 한 장(쉬운 말·큰 글씨·읽어주기) | `/card/{id}` | 소비자 |
| 두 제품 나란히 비교 | `/compare?a=1&b=2` | 소비자 |
| 확인 방법과 한계 | `/about` | 심사위원 |
| 제품 등록 → 카드 초안 생성 | `/new` | 검토자 |
| 검토·승인·공공 DB 입력 | `/review`, `/review/{id}` | 검토자 |
| 카드 JSON | `/api/card/{id}.json` | 개발자 |
| 라벨링용 CSV | `/export/claims.csv` | 평가 |

## LLM 키

`.env`에 셋 중 하나를 넣는다. 없으면 `LLM_PROVIDER=rules` 데모 모드로 돌아간다(키워드·수치 규칙만 사용, 이미지 OCR 없음).

```
GEMINI_API_KEY=AQ...  또는 AIza...  # Google AI Studio·Vertex 키 모두 됨(두 주소를 차례로 시도)
ANTHROPIC_API_KEY=sk-ant-...        # 또는
OPENAI_API_KEY=sk-...
REVIEW_TOKEN=아무문자열             # 공개 배포 시 검토자 화면 보호. /review?token=값 으로 한 번 접속하면 쿠키에 저장
```

## 파이프라인 (app/pipeline/)

```
ingest.py    URL·PDF·텍스트 → 문장 청크. 이미지 상세페이지는 비전 LLM으로 OCR
extract.py   판매페이지에서 AI 표시 문구 추출(규칙으로 후보 축소 → LLM 선별·분류)
retrieve.py  문자 2-gram BM25로 근거 후보 검색(설명서·사양표·공공DB 우선)
link.py      문구별 근거 연결 + 4단계 확인상태 판정. 광고 문구 반복은 근거로 치지 않음
coverage.py  권익 5항목(조건·데이터·해제·안전·피해구제) 공개 여부 판정
easy.py      쉬운 말 보기(검토된 사실 요약 공유, 조건·수치·예외 보존)
run.py       오케스트레이션. 재생성해도 검토자 수정과 공공 DB 입력은 보존
```

판정 원칙은 `llm.py`의 SYSTEM_PROMPT에 있다. 우열·진위·위법 판단 금지, 원문 인용만, 불확실하면 '자료 부족'.

## 평가 (eval/)

- `evaluate.py` — 가설 1(기술). 라벨 CSV와 AI 판정을 비교해 확인상태 정확도, 근거 연결 정확도, 근거 없는 공개 판단 수를 낸다.
  ```bash
  # 1) /export/claims.csv 내려받아 gold_status, gold_evidence_quote 채우기
  python eval/evaluate.py claims_labels.csv --out eval/result.md
  python eval/evaluate.py claims_labels.csv --use-reviewer   # 사람+AI 협업 결과
  ```
- `usability_protocol.md`, `usability/` — 사용성 시험 설계 및 가상 검토 자료. 실제 참여자 측정 증거로 사용하지 않는다.
- `heldout3~7/` — 블라인드 라벨링 라운드별 자료: 라벨러에게 간 자료(판정 없음), 정답 다수결, 채점 결과, 사전 예상 기록. 규칙 수정 이력은 `heldout4/dev_rounds/README.md`.
- 시험 세트는 측정 후 규칙을 고쳐도 수치를 바꾸지 않는다(사전 등록 원칙). 작성자 독립성과 실제 참여 과정은 별도 검증 전까지 확정하지 않는다.

## 제품 등록 순서(검토자)

1. `/new`에서 제품명·모델명, 판매페이지 URL(또는 본문 붙여넣기), 설명서 URL/PDF 입력 → 카드 초안 생성
2. `/review/{id}`에서 문구마다 출처·근거 원문을 열어 보고 판정을 확정. AI 표시가 아닌 문구는 제외
3. 모델명으로 KC·전파인증·효율등급·리콜을 직접 조회해 '공공 DB 교차확인'에 기록
4. '카드 다시 생성'으로 쉬운 말 문장을 갱신 → '승인하고 공개'

## 배포(테스트 URL 제출용)

Render / Railway / Fly.io 어디든 된다. 시작 명령: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
환경변수에 LLM 키와 `REVIEW_TOKEN`을 넣고, `AICARD_DB`를 영속 디스크 경로로 지정한다. 첫 배포 후 `python seed.py` 한 번.

## 하지 않는 것

제품 내부 구현 추정, 등급 매기기, 법 위반 판정, 미승인 결과 공개, 소비자 개인정보 수집.
쉬운 말·큰 글씨 설정은 이용자 브라우저에만 저장된다.

## 2026-10-13 정정본

`RELEASE_REVIEW.md`에 검수 범위와 제한을 기록했다. `docs/submission/`의 10월 12일 보고서·영상 대본 및 `eval/usability/results/`는 정정 전 역사 자료로, 사용성 실증 성과 문구를 그대로 제출하거나 영상에 사용하면 안 된다. 원본은 보존한다.
