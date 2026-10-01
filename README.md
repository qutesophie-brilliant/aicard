# AI 정보카드 — AI 표시, 근거까지 읽기

2026 AI 라이프 솔루션 챌린지 출품용 프로토타입.
제품 판매페이지의 'AI' 표시 문구를 하나씩 꺼내 설명서·사양표·공공 DB에서 근거를 찾아 연결하고,
근거 확인 / 조건 확인 / 자료 부족 / 추가 확인 필요 네 가지 확인상태와 소비자 권익 7항목을
한 장의 카드로 보여 준다. 사람이 승인한 카드만 공개된다.

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
easy.py      쉬운 말 버전(25자 안팎 문장, 평가·권고 표현 금지)
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
- `usability_protocol.md` — 가설 2·3(효과·포용). 소비자 5명 전후 비교, 60대 이상 쉬운 말 모드 과제.

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
