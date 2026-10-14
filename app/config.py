"""환경 설정. .env 파일 또는 환경변수로 지정한다."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("AICARD_DB", DATA_DIR / "aicard.sqlite3"))


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# LLM 공급자: gemini | anthropic | openai | rules
#   rules = 키 없이 규칙 기반으로만 동작하는 데모 모드
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").lower() or (
    "gemini" if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") else
    "anthropic" if os.getenv("ANTHROPIC_API_KEY") else
    "openai" if os.getenv("OPENAI_API_KEY") else "rules"
)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY", "")
# 앞에서부터 차례로 시도한다. 모델이 단종되거나(404) 혼잡하면(503·429) 다음 모델로 넘어간다.
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODEL", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-flash-latest,gemini-3.5-flash,gemini-3.1-flash-lite,gemini-flash-lite-latest").split(",") if m.strip()]
GEMINI_MODEL = GEMINI_MODELS[0]
# AQ. 으로 시작하는 키는 Vertex AI(express) 키일 수 있어 두 주소를 차례로 시도한다
GEMINI_ENDPOINTS = [e for e in os.getenv("GEMINI_ENDPOINT", "").split(",") if e] or [
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent",
]
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# 이미지(상세페이지) OCR을 비전 LLM으로 수행할 최대 이미지 수
MAX_OCR_IMAGES = int(os.getenv("MAX_OCR_IMAGES", "12"))

# 소비자 '바로 확인'의 캡처 업로드. 비전 모델 OCR은 크레딧을 쓰므로 기본은 켬(키가 있을 때)·시간당 횟수 제한.
CHECK_OCR_LLM = os.getenv("CHECK_OCR_LLM", "1") == "1"
# 즉석 확인에서 문구 추출·근거 연결에 LLM을 쓸지(끄면 규칙만, 크레딧 0). 시간당 IP별 횟수를 넘으면 규칙으로 떨어진다.
CHECK_LLM = os.getenv("CHECK_LLM", "0") == "1"
CHECK_LLM_PER_HOUR = int(os.getenv("CHECK_LLM_PER_HOUR", "6"))
CHECK_OCR_PER_HOUR = int(os.getenv("CHECK_OCR_PER_HOUR", "8"))
CHECK_MAX_FILES = int(os.getenv("CHECK_MAX_FILES", "8"))
CHECK_MAX_FILE_MB = int(os.getenv("CHECK_MAX_FILE_MB", "8"))

# 검토자 화면 보호용 토큰. 비어 있으면 누구나 접근 가능(로컬 개발용).
REVIEW_TOKEN = os.getenv("REVIEW_TOKEN", "")

# 확인상태 4종 — 1차 제안서 <표 2>와 동일
STATUSES = ["근거 확인", "조건 확인", "자료 부족", "추가 확인 필요"]

STATUS_HELP = {
    "근거 확인": "회사가 공개한 자료에 이 문구를 뒷받침하는 설명이 있어요.",
    "조건 확인": "뒷받침 설명이 있고, 작동 조건이나 한계도 함께 적혀 있어요.",
    "자료 부족": "회사가 공개한 자료에서 설명을 찾지 못했어요. 기능이 없다거나 표시가 거짓이라고 판정한 것은 아니에요.",
    "추가 확인 필요": "표시 문구와 회사 설명이 서로 맞지 않아요. 사람이 더 확인해야 하는 문구예요.",
}

# 소비자 권익 7항목 — 1차 제안서 '소비자 정보카드' 구성
CARD_ITEMS = [
    ("functions", "AI 기능·목적", "이 제품에서 'AI'라고 표시한 기능은 무엇이고, 무엇을 위한 것인가"),
    ("evidence", "표시 근거와 확인상태", "각 AI 표시 문구에 사업자가 제시한 설명이 있는가"),
    ("conditions", "작동 조건·한계", "어떤 조건에서 작동하고, 어떤 경우에 작동하지 않는가"),
    ("data", "수집 데이터·처리 위치·학습 이용", "어떤 데이터를 모으고, 어디서 처리하며, 학습에 쓰는가"),
    ("optout", "AI 기능 해제와 대체모드", "AI 기능을 끌 수 있는가, 끄면 어떻게 동작하는가"),
    ("safety", "오작동 안전장치·업데이트·지원", "오작동 시 보호장치, 소프트웨어 업데이트·지원 기간"),
    ("remedy", "문의·환불·보상 등 피해구제", "문제가 생겼을 때 어디에, 어떻게 연락하고 구제받는가"),
]

# 2차 개선: 추출 결과 재검토 호출(카드당 1회 추가). 0이면 끔.
EXTRACT_VERIFY = os.getenv("EXTRACT_VERIFY", "0") == "1"  # 추출 재검토 호출(정밀도용). 재현율을 깎아 기본은 끔
# 추출을 몇 번 돌려 합칠지. LLM이 고르는 문장 수가 매번 달라(같은 제품에서 3~7개) 2번 돌려 합치면 재현율이 안정된다. 카드당 호출 +1
EXTRACT_PASSES = max(1, int(os.getenv("EXTRACT_PASSES", "2") or 2))
