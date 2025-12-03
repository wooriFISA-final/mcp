import os
from pathlib import Path
import re
import logging
import pandas as pd
from datetime import datetime
from fastapi import APIRouter, Body
from typing import Dict, Any, List, Optional
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings
# from langchain_huggingface import HuggingFaceEndpointEmbeddings
import faiss
import pickle
from langchain_ollama import OllamaEmbeddings
from sqlalchemy import create_engine, text
import torch
import gc
import httpx
import numpy as np
from typing import List
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from dotenv import load_dotenv

# 🔹 스키마 임포트
from server.schemas.plan_schema import (
    ParseCurrencyRequest,
    ParseCurrencyResponse,
    HealthResponse,
    NormalizeLocationRequest,
    NormalizeLocationResponse,
    ParseRatioRequest,
    ParseRatioResponse,
    ValidateInputRequest,
    ValidateInputResponse,
    RecommendDepositSavingProductsRequest,
    RecommendDepositSavingProductsResponse,
    SelectTopFundsByRiskRequest,
    SelectTopFundsByRiskResponse,
    CalcShortageAmountRequest,
    CalcShortageAmountResponse,
    SimulateInvestmentRequest,
    SimulateInvestmentResponse,
    GetSavingsCandidatesRequest,
    GetSavingsCandidatesResponse,
    CheckPlanCompletionRequest,
    CheckPlanCompletionResponse,
    ValidateSelectedSavingsProductsRequest,
    ValidateSelectedSavingsProductsResponse,
    ValidateSelectedFundsProductsRequest,
    ValidateSelectedFundsProductsResponse,
    CalculatePortfolioAmountsRequest,
    CalculatePortfolioAmountsResponse,
    CalculateLTVRequest,
    CalculateLTVResponse,
    GetLoanProductRequest,
    GetLoanProductResponse,
    CalculateFinalLoanRequest,
    CalculateFinalLoanResponse,
)

# 라우터 설정
router = APIRouter(
    prefix="/input",  # API 엔드포인트 기본 경로
    tags=["PlanInput & Validation Tools"],  # Swagger UI용 카테고리 표시
)

logger = logging.getLogger(__name__)
load_dotenv()
_embeddings: Optional[Embeddings] = None  # 전역 캐시

# ==========================================
# 🔹 FAISS 예/적금 인덱스 로더
#    - faiss_deposit_products / faiss_saving_products
#    - 각 폴더에 index.faiss + index.pkl 있다고 가정
# ==========================================
BASE_DIR = Path(__file__).resolve().parents[2]
FAISS_DEPOSIT_DIR = BASE_DIR / "faiss_deposit_products"
FAISS_SAVING_DIR = BASE_DIR / "faiss_saving_products"

# 전역 캐시
_deposit_store: Optional[FAISS] = None
_saving_store: Optional[FAISS] = None

# ============================================================
# FAISS 전역 캐시 (plan_agent_tools용)
# ============================================================
_plan_deposit_index = None
_plan_deposit_metadata = None
_plan_saving_index = None
_plan_saving_metadata = None


DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

# db_url = f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
# engine = create_engine(db_url)
# 개선된 엔진 설정
engine = create_engine(
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}",
    poolclass=QueuePool,
    pool_size=5,                    # 기본 연결 풀 크기
    max_overflow=10,                # 추가 연결 최대 개수
    pool_timeout=30,                # 연결 대기 타임아웃
    pool_recycle=3600,              # 1시간마다 연결 재생성 (MySQL wait_timeout 대응)
    pool_pre_ping=True,             # ⭐ 중요: 쿼리 전 연결 유효성 검사
    connect_args={
        "connect_timeout": 10,      # 연결 타임아웃 10초
    },
    echo=False,                     # 개발 시 True로 설정하면 SQL 로깅
)

# 임베딩 API 설정
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL")
EMBEDDING_API_TIMEOUT = 30.0

# 기존 모델 로드 함수 제거하고 API 호출 함수로 대체
async def _get_embeddings_from_api(texts: List[str], normalize: bool = True) -> np.ndarray:
    """
    PC 서버의 임베딩 API를 호출하여 임베딩 생성
    
    Args:
        texts: 임베딩할 텍스트 리스트
        normalize: 정규화 여부
    
    Returns:
        numpy array of embeddings
    """
    try:
        async with httpx.AsyncClient(timeout=EMBEDDING_API_TIMEOUT) as client:
            response = await client.post(
                f"{EMBEDDING_API_URL}/embed",
                json={
                    "texts": texts,
                    "normalize": normalize
                }
            )
            response.raise_for_status()
            
            data = response.json()
            embeddings = np.array(data["embeddings"], dtype=np.float32)
            
            logger.info(f"✅ 임베딩 API 호출 성공 (dimension: {data['dimension']})")
            return embeddings
            
    except httpx.RequestError as e:
        logger.error(f"❌ 임베딩 API 연결 실패: {e}")
        raise ConnectionError(f"임베딩 서버에 연결할 수 없습니다: {EMBEDDING_API_URL}")
    except httpx.HTTPStatusError as e:
        logger.error(f"❌ 임베딩 API 오류: {e}")
        raise ValueError(f"임베딩 생성 실패: {e.response.text}")
    except Exception as e:
        logger.error(f"❌ 임베딩 생성 중 예상치 못한 오류: {e}")
        raise


# FAISS 로드 함수는 그대로 유지
def _load_plan_deposit_faiss():
    """예금 FAISS 인덱스 로드 (plan_agent_tools용)"""
    global _plan_deposit_index, _plan_deposit_metadata
    
    if _plan_deposit_index is None:
        data_dir = Path(__file__).resolve().parents[2] / "rag"
        index_path = data_dir / "faiss_deposit_products" / "index.faiss"
        metadata_path = data_dir / "faiss_deposit_products" / "index.pkl"
        
        if not index_path.exists() or not metadata_path.exists():
            raise FileNotFoundError(f"예금 FAISS 인덱스를 찾을 수 없습니다: {index_path}")
        
        logger.info(f"📥 예금 FAISS 인덱스 로드 중: {index_path}")
        _plan_deposit_index = faiss.read_index(str(index_path))
        
        with open(metadata_path, "rb") as f:
            _plan_deposit_metadata = pickle.load(f)
        
        # ✅ LangChain FAISS 구조: (index_to_docstore_id, docstore)
        if isinstance(_plan_deposit_metadata, tuple) and len(_plan_deposit_metadata) == 2:
            index_to_id, docstore = _plan_deposit_metadata
            logger.info(f"✅ index_to_docstore_id 타입: {type(index_to_id)}")
            logger.info(f"✅ docstore 타입: {type(docstore)}")
            
            # docstore의 내용 확인
            if hasattr(docstore, '_dict'):
                logger.info(f"✅ docstore 문서 개수: {len(docstore._dict)}")
                # 첫 번째 문서 샘플 확인
                if docstore._dict:
                    first_key = list(docstore._dict.keys())[0]
                    first_doc = docstore._dict[first_key]
                    logger.info(f"✅ 첫 번째 문서 타입: {type(first_doc)}")
                    logger.info(f"✅ 첫 번째 문서 샘플: {first_doc}")
        
        logger.info(f"✅ 예금 인덱스 로드 완료 ({_plan_deposit_index.ntotal}개 벡터)")
    
    return _plan_deposit_index, _plan_deposit_metadata


def _load_plan_saving_faiss():
    """적금 FAISS 인덱스 로드 (plan_agent_tools용)"""
    global _plan_saving_index, _plan_saving_metadata
    
    if _plan_saving_index is None:
        data_dir = Path(__file__).resolve().parents[2] / "rag"
        index_path = data_dir / "faiss_saving_products" / "index.faiss"
        metadata_path = data_dir / "faiss_saving_products" / "index.pkl"
        
        if not index_path.exists() or not metadata_path.exists():
            raise FileNotFoundError(f"적금 FAISS 인덱스를 찾을 수 없습니다: {index_path}")
        
        logger.info(f"📥 적금 FAISS 인덱스 로드 중: {index_path}")
        _plan_saving_index = faiss.read_index(str(index_path))
        
        with open(metadata_path, "rb") as f:
            _plan_saving_metadata = pickle.load(f)
        
        # ✅ LangChain FAISS 구조: (index_to_docstore_id, docstore)
        if isinstance(_plan_saving_metadata, tuple) and len(_plan_saving_metadata) == 2:
            index_to_id, docstore = _plan_saving_metadata
            logger.info(f"✅ index_to_docstore_id 타입: {type(index_to_id)}")
            logger.info(f"✅ docstore 타입: {type(docstore)}")
            
            # docstore의 내용 확인
            if hasattr(docstore, '_dict'):
                logger.info(f"✅ docstore 문서 개수: {len(docstore._dict)}")
                # 첫 번째 문서 샘플 확인
                if docstore._dict:
                    first_key = list(docstore._dict.keys())[0]
                    first_doc = docstore._dict[first_key]
                    logger.info(f"✅ 첫 번째 문서 타입: {type(first_doc)}")
                    logger.info(f"✅ 첫 번째 문서 샘플: {first_doc}")
        
        logger.info(f"✅ 적금 인덱스 로드 완료 ({_plan_saving_index.ntotal}개 벡터)")
    
    return _plan_saving_index, _plan_saving_metadata


def _build_search_query_from_user(user_profile: Dict[str, Any]) -> str:
    """
    사용자 프로필 정보를 바탕으로 FAISS 검색용 자연어 쿼리 생성
    
    예: "30세 직장인으로 안정형 투자 성향이며 5천만원의 목표 자금을 모으기 위한 저축 상품"
    """
    parts = []
    
    # 나이
    age = user_profile.get("age")
    if age:
        parts.append(f"{age}세")
    
    # 직업
    job = user_profile.get("job", "")
    if job:
        parts.append(f"{job}")
    
    # 투자 성향
    invest_tendency = user_profile.get("invest_tendency", "")
    if invest_tendency:
        parts.append(f"{invest_tendency} 투자 성향")
    
    # 부족 자금 (목표 금액)
    shortage_amount = user_profile.get("shortage_amount", 0)
    if shortage_amount and shortage_amount > 0:
        if shortage_amount >= 100_000_000:  # 1억 이상
            amount_str = f"{shortage_amount // 100_000_000}억"
            if shortage_amount % 100_000_000 > 0:
                amount_str += f" {(shortage_amount % 100_000_000) // 10_000_000}천만"
        else:
            amount_str = f"{shortage_amount // 10_000_000}천만"
        parts.append(f"{amount_str}원의 목표 자금을 모으기 위한")
    
    parts.append("저축 상품")
    
    query = " ".join([p for p in parts if p])
    return query


# 1. 금액 파싱 Tool
@router.post(
    "/parse_currency",
    summary="한국어 금액 단위 변환",
    operation_id="parse_currency",
    description=(
        "한국어 금액 단위(억, 천만, 만 등)를 원 단위 정수로 변환합니다.\n\n"
        "예:\n"
        "- '3억 5천만' → 350000000\n"
        "- '1200만' → 12000000"
    ),
    response_model=ParseCurrencyResponse,
)
async def api_parse_currency(
    req: ParseCurrencyRequest = Body(...),
) -> ParseCurrencyResponse:
    # 엔드포인트 내부에 파서 함수를 중첩 정의
    def _parse_korean_currency(v: Any) -> int:
        """'3억 5천' 같은 금액 표현을 정수(원)로 변환"""
        if v is None or v == "":
            return 0
        if isinstance(v, (int, float)):
            return int(v)

        text = str(v).strip().replace(",", "").replace(" ", "")
        if text == "":
            return 0

        # 숫자만이면 그대로
        if re.fullmatch(r"\d+", text):
            return int(text)

        total = 0.0
        for pattern, multiplier in [
            (r"(\d+(?:\.\d+)?)억", 100_000_000),
            (r"(\d+(?:\.\d+)?)천만", 10_000_000),
            (r"(\d+(?:\.\d+)?)백만", 1_000_000),
            (r"(\d+(?:\.\d+)?)만", 10_000),
        ]:
            m = re.search(pattern, text)
            if m:
                total += float(m.group(1)) * multiplier

        if total == 0:
            # 단위가 없는데 숫자+문자 혼합이면 숫자만 추출
            digits = re.sub(r"[^0-9]", "", text)
            try:
                return int(float(digits)) if digits else 0
            except ValueError:
                return 0

        return int(total)

    try:
        parsed = _parse_korean_currency(req.value)
        return ParseCurrencyResponse(
            success=True,
            parsed=parsed,
            error=None,
        )
    except Exception as e:
        logger.exception("parse_currency 실패")
        return ParseCurrencyResponse(
            success=False,
            parsed=0,
            error=str(e),
        )

def _get_embeddings() -> Embeddings:
    """
    ⚠️ 중요: FAISS 인덱스를 만들 때 사용한 임베딩 모델과 동일해야 함.
    여기서는 Hugging Face Inference API의 Qwen/Qwen3-Embedding-8B 사용.
    """
    global _embeddings
    if _embeddings is None:
        embed_model = os.getenv("EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
        hf_token = os.getenv("HF_TOKEN")

        if not hf_token:
            raise RuntimeError(
                "HF_TOKEN 이 설정되어 있지 않습니다. "
                ".env 에 토큰을 추가하거나 환경변수로 설정하세요."
            )

        _embeddings = HuggingFaceEndpointEmbeddings(
            model=embed_model,
            task="feature-extraction",  # HF 임베딩 엔드포인트 기본 태스크
            huggingfacehub_api_token=hf_token,
        )

        logger.info(f"✅ HF Embeddings 로드 완료: {embed_model}")

    return _embeddings


def _get_faiss_store(kind: str) -> FAISS:
    """
    kind: 'deposit' | 'saving'
    해당 폴더에서 index.faiss + index.pkl을 이용해 LangChain FAISS 로드
    """
    global _deposit_store, _saving_store

    embeddings = _get_embeddings()

    if kind == "deposit":
        if _deposit_store is None:
            logger.info(f"🔄 예금 FAISS 인덱스 로드: {FAISS_DEPOSIT_DIR}")
            _deposit_store = FAISS.load_local(
                str(FAISS_DEPOSIT_DIR),
                embeddings,
                allow_dangerous_deserialization=True,
            )
        return _deposit_store

    elif kind == "saving":
        if _saving_store is None:
            logger.info(f"🔄 적금 FAISS 인덱스 로드: {FAISS_SAVING_DIR}")
            _saving_store = FAISS.load_local(
                str(FAISS_SAVING_DIR),
                embeddings,
                allow_dangerous_deserialization=True,
            )
        return _saving_store

    else:
        raise ValueError(f"Unknown FAISS kind: {kind}")


def _build_user_profile_text(user_data: Dict[str, Any]) -> str:
    """
    사용자 프로필(dict)을 자연어 텍스트로 변환해서 검색 질의로 사용.
    인덱스를 만들 때 '상품 설명' 기준으로 임베딩했을 것이므로,
    여기서는 '어떤 사람이 어떤 목적의 상품을 찾는지'를 묘사해 준다는 느낌.
    """
    age = user_data.get("age")
    salary = user_data.get("salary")
    invest_tendency = user_data.get("invest_tendency") or user_data.get("risk_type")
    goal = user_data.get("goal") or user_data.get("purpose") or "주택 자금 마련"

    parts = []
    if age:
        parts.append(f"{age}세")
    if salary:
        parts.append(f"연봉 {salary}원")
    if invest_tendency:
        parts.append(f"투자 성향은 {invest_tendency}")
    parts.append(goal)
    # 예: "29세, 연봉 42000000원, 투자 성향은 안정형, 주택 자금 마련"
    return ", ".join(parts)


# 1. 금액 파싱 Tool
@router.post(
    "/parse_currency",
    summary="한국어 금액 단위 변환",
    operation_id="parse_currency",
    description=(
        "한국어 금액 단위(억, 천만, 만 등)를 원 단위 정수로 변환합니다.\n\n"
        "예:\n"
        "- '3억 5천만' → 350000000\n"
        "- '1200만' → 12000000"
    ),
    response_model=ParseCurrencyResponse,
)
async def api_parse_currency(
    req: ParseCurrencyRequest = Body(...),
) -> ParseCurrencyResponse:
    # 엔드포인트 내부에 파서 함수를 중첩 정의
    def _parse_korean_currency(v: Any) -> int:
        """'3억 5천' 같은 금액 표현을 정수(원)로 변환"""
        if v is None or v == "":
            return 0
        if isinstance(v, (int, float)):
            return int(v)

        text = str(v).strip().replace(",", "").replace(" ", "")
        if text == "":
            return 0

        # 숫자만이면 그대로
        if re.fullmatch(r"\d+", text):
            return int(text)

        total = 0.0
        for pattern, multiplier in [
            (r"(\d+(?:\.\d+)?)억", 100_000_000),
            (r"(\d+(?:\.\d+)?)천만", 10_000_000),
            (r"(\d+(?:\.\d+)?)백만", 1_000_000),
            (r"(\d+(?:\.\d+)?)만", 10_000),
        ]:
            m = re.search(pattern, text)
            if m:
                total += float(m.group(1)) * multiplier

        if total == 0:
            # 단위가 없는데 숫자+문자 혼합이면 숫자만 추출
            digits = re.sub(r"[^0-9]", "", text)
            try:
                return int(float(digits)) if digits else 0
            except ValueError:
                return 0

        return int(total)

    try:
        parsed = _parse_korean_currency(req.value)
        return ParseCurrencyResponse(
            success=True,
            parsed=parsed,
            error=None,
        )
    except Exception as e:
        logger.exception("parse_currency 실패")
        return ParseCurrencyResponse(
            success=False,
            parsed=0,
            error=str(e),
        )


# 2. 헬스 체크 엔드포인트
@router.get(
    "/health",
    summary="상태 점검(Health Check)",
    operation_id="plan_health",
    description=(
        "PlanInput 관련 툴 서버의 동작 상태를 확인합니다.\n\n"
        "출력 필드:\n"
        "- success: 헬스 체크 성공 여부(Boolean)\n"
        "- llm_model: 사용 중인 LLM 모델명 (환경변수 PLAN_LLM, 기본값 'qwen3:8b')\n\n"
        "응답 예시:\n"
        '{"success": true, "llm_model": "qwen3:8b"}'
    ),
    response_model=HealthResponse,
)
async def api_health() -> HealthResponse:
    try:
        llm_model = os.getenv("PLAN_LLM", "qwen3:8b")
        return HealthResponse(
            success=True,
            llm_model=llm_model,
            error=None,
        )
    except Exception as e:
        logger.exception("health 실패")
        return HealthResponse(
            success=False,
            llm_model=None,
            error=str(e),
        )


# 3. 지역 정규화 Tool
@router.post(
    "/normalize_location",
    summary="지역명 정규화",
    operation_id="normalize_location",
    description=(
        "자유 형식의 지역명을 표준 행정구역명으로 정규화합니다.\n\n"
        "규칙 예시:\n"
        "- '서울'이 포함되면 '서울특별시 {구}' 형태로 보정\n"
        "- 광역시는 '○○광역시', 도는 '○○도'로 표기\n\n"
        "입력/출력 예시:\n"
        "- '서울 동작구' → '서울특별시 동작구'\n"
        "- '부산 해운대구' → '부산광역시 해운대구'"
    ),
    response_model=NormalizeLocationResponse,
)
async def normalize_location(
    req: NormalizeLocationRequest = Body(...),
) -> NormalizeLocationResponse:
    """간단한 지역명 매핑"""
    try:
        mapping = {
            "서울 동작구": "서울특별시 동작구",
            "서울 마포구": "서울특별시 마포구",
            "서울 송파구": "서울특별시 송파구",
            "부산 해운대구": "부산광역시 해운대구",
            "대구 수성구": "대구광역시 수성구",
        }
        normalized = mapping.get(req.location.strip(), req.location)
        return NormalizeLocationResponse(
            success=True,
            normalized=normalized,
            error=None,
        )
    except Exception as e:
        logger.error(f"normalize_location Error: {e}")
        return NormalizeLocationResponse(
            success=False,
            normalized=req.location,
            error=str(e),
        )


# 4. 퍼센트/비율 파싱 Tool
@router.post(
    "/parse_ratio",
    summary="비율(%) 정수 변환",
    operation_id="parse_ratio",
    description=(
        "퍼센트(%)가 포함되었든 아니든, 비율 값을 정수로 정규화합니다.\n\n"
        "입력/출력 예시:\n"
        "- '30%' → 30\n"
        "- '15'  → 15\n"
        "- ' 40 % ' → 40\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부(Boolean)\n"
        "- ratio: 정수 비율 값"
    ),
    response_model=ParseRatioResponse,
)
async def parse_ratio(
    req: ParseRatioRequest = Body(...),
) -> ParseRatioResponse:
    """'30%' 또는 '20' 같은 입력을 정수 비율로 변환"""
    try:
        if not req.value:
            return ParseRatioResponse(
                success=False,
                ratio=0,
                error=None,
            )
        ratio = int(str(req.value).replace("%", "").strip())
        return ParseRatioResponse(
            success=True,
            ratio=ratio,
            error=None,
        )
    except Exception as e:
        logger.error(f"parse_ratio Error: {e}")
        return ParseRatioResponse(
            success=False,
            ratio=0,
            error=str(e),
        )


# 5. 입력 검증 Tool (input + validation 통합)
@router.post(
    "/validate_input_data",
    summary="주택 계획 입력값 검증·정규화",
    operation_id="validate_input_data",
    description=(
        "입력된 원시(raw) 데이터를 받아 **누락 필드 점검** 후, "
        "**금액·비율·지역** 값을 표준 형태로 정규화합니다. (DB/시세조회 미포함)\n\n"
        "⚠️ **요청 형식 (두 가지 모두 지원):**\n\n"
        "**방식 1: 평탄한 구조 (권장)**\n"
        "```json\n"
        "{\n"
        '  "initial_prop": "3천만",\n'
        '  "hope_location": "서울 동작구",\n'
        '  "hope_price": "7억",\n'
        '  "hope_housing_type": "아파트",\n'
        '  "income_usage_ratio": "30%"\n'
        "}\n"
        "```\n\n"
        "**방식 2: 래퍼 구조**\n"
        "```json\n"
        "{\n"
        '  "data": {\n'
        '    "initial_prop": "3천만",\n'
        '    "hope_location": "서울 동작구",\n'
        '    "hope_price": "7억",\n'
        '    "hope_housing_type": "아파트",\n'
        '    "income_usage_ratio": "30%"\n'
        "  }\n"
        "}\n"
        "```\n\n"
        "출력 필드:\n"
        "- status: 'success' | 'incomplete' | 'error'\n"
        "- data: 정규화된 결과 (success일 때)\n"
        "- missing_fields: 누락된 필드 목록 (incomplete일 때)"
    ),
    response_model=ValidateInputResponse,
)
async def validate_input_data(
    payload: ValidateInputRequest = Body(...),  # ✅ ValidateInputRequest 유지
) -> ValidateInputResponse:
    """
    전체 입력 데이터의 누락 필드를 검사하고,
    금액·비율·지역 정보를 표준화하여 반환.
    """
    try:
        # ✅ model_validator가 이미 data 구조로 통일했음
        data = payload.data
        
        if not data:
            return ValidateInputResponse(
                success=False,
                status="error",
                data=None,
                missing_fields=[],
                message="입력 데이터가 제공되지 않았습니다.",
            )
        
        result_missing: List[str] = []

        # 필수 입력 필드 정의
        required_fields = [
            "initial_prop",
            "hope_location",
            "hope_price",
            "hope_housing_type",
            "income_usage_ratio",
        ]

        # 누락 필드 검증
        for field in required_fields:
            value = data.get(field)
            if value in [None, "", 0, "0"]:
                result_missing.append(field)

        # 필드 누락 시 즉시 반환
        if result_missing:
            return ValidateInputResponse(
                success=False,
                status="incomplete",
                data=None,
                missing_fields=result_missing,
                message=None,
            )

        # 각 필드별 정규화 수행
        from fastapi.encoders import jsonable_encoder

        cur1 = await api_parse_currency(
            ParseCurrencyRequest(value=data.get("initial_prop", "0"))
        )
        cur2 = await api_parse_currency(
            ParseCurrencyRequest(value=data.get("hope_price", "0"))
        )
        ratio = await parse_ratio(
            ParseRatioRequest(value=data.get("income_usage_ratio", "0"))
        )
        loc = await normalize_location(
            NormalizeLocationRequest(location=data.get("hope_location", ""))
        )

        # 정규화 완료된 결과 구성
        normalized_data = jsonable_encoder(
            {
                "initial_prop": cur1.parsed,
                "hope_location": loc.normalized,
                "hope_price": cur2.parsed,
                "hope_housing_type": data.get("hope_housing_type"),
                "income_usage_ratio": ratio.ratio,
                "validation_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

        return ValidateInputResponse(
            success=True,
            status="success",
            data=normalized_data,
            missing_fields=[],
            message=None,
        )

    except Exception as e:
        logger.error(f"validate_input_data Error: {e}")
        return ValidateInputResponse(
            success=False,
            status="error",
            data=None,
            missing_fields=[],
            message=str(e),
        )


# 6. 입력 완료 여부 판단 Tool
@router.post(
    "/check_plan_completion",
    summary="주택 계획 입력 완료 여부 판단",
    operation_id="check_plan_completion",
    description=(
        "대화 메시지 히스토리를 기반으로 주택 자금 계획 입력이 완료되었는지를 판단합니다.\n\n"
        "기본 동작:\n"
        "- 마지막 assistant/ai 메시지가 '정리해 보면'으로 시작하면 완료로 간주합니다.\n"
        "- 그 외에는 미완료로 간주하고 is_complete=False 를 반환합니다.\n\n"
        "향후에는 LLM을 사용해 5개 필드(initial_prop, hope_location, hope_price, "
        "hope_housing_type, income_usage_ratio)의 실제 채워짐 여부를 더 정교하게 판단하도록 확장할 수 있습니다."
    ),
    response_model=CheckPlanCompletionResponse,
)
async def check_plan_completion(
    payload: CheckPlanCompletionRequest = Body(...),
) -> CheckPlanCompletionResponse:
    """
    PlanInputAgent 대화 히스토리(messages)를 받아,
    마지막 assistant/ai 발화가 '정리해 보면'으로 시작하는지 여부를 기준으로
    입력 완료 여부를 판단하는 간단한 Tool.
    """
    try:
        messages = payload.messages or []
        is_complete = False
        summary_text: Optional[str] = None

        # 뒤에서부터 assistant/ai 메시지 찾기
        for msg in reversed(messages):
            role = (msg.get("role") or "").lower()
            content = (msg.get("content") or "").strip()

            if role in ("assistant", "ai"):
                if content.startswith("정리해 보면"):
                    is_complete = True
                    summary_text = content
                break

        return CheckPlanCompletionResponse(
            success=True,
            is_complete=is_complete,
            missing_fields=[],
            summary_text=summary_text,
            error=None,
        )
    except Exception as e:
        logger.error(f"check_plan_completion Error: {e}", exc_info=True)
        return CheckPlanCompletionResponse(
            success=False,
            is_complete=False,
            missing_fields=[],
            summary_text=None,
            error=str(e),
        )


# 7. 예·적금 Top3 필터링 Tool (CSV + 조건 필터링)
@router.post(
    "/filter_top_products",
    summary="예·적금 Top3 상품 필터링",
    operation_id="filter_top_savings_products",
    description=(
        "사용자의 나이, 첫거래 여부, 목표 기간(개월)을 기준으로 예·적금 상품 CSV에서 "
        "조건에 맞는 상품을 필터링하고, 각각 **Top3 (금리 기준)**를 반환합니다.\n\n"
        "입력 필드 예시:\n"
        "- user_data.age: 사용자 나이 (예: 32)\n"
        "- user_data.is_first_customer: 첫 거래 여부 (예: true/false)\n"
        "- user_data.period_goal_months: 목표 기간(개월) (예: 12)\n"
        "- csv_file_path (선택): CSV 경로 (미지정 시 기본값 사용)\n\n"
        "CSV 컬럼 예시:\n"
        "- product_type: '예금' 또는 '적금'\n"
        "- condition_min_age: 가입 최소 나이\n"
        "- condition_first_customer: 첫 거래 고객 전용 여부(Boolean)\n"
        "- min_term, max_term: 가입 가능 최소/최대 기간(개월)\n"
        "- max_rate: 최대 금리\n"
        "- name, description 등 기타 정보\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부(Boolean)\n"
        "- top_deposits: 조건에 맞는 예금 Top3 리스트\n"
        "- top_savings: 조건에 맞는 적금 Top3 리스트\n"
        "- meta: 사용된 CSV 경로, 필터링된 상품 수 등 부가정보"
    ),
    response_model=dict,
)

# ============================================================
# 10. [FAISS] 사용자 정보 기반 예금/적금 추천 (3개씩)
# ============================================================
@router.post(
    "/recommend_deposit_saving_products",
    summary="사용자 맞춤 예금/적금 상품 추천",
    operation_id="recommend_deposit_saving_products",
    response_model=RecommendDepositSavingProductsResponse,
)
async def api_recommend_deposit_saving_products(
    payload: RecommendDepositSavingProductsRequest = Body(...),
) -> RecommendDepositSavingProductsResponse:
    """사용자 정보 기반 FAISS 검색으로 예금 3개, 적금 3개 추천"""
    try:
        user_profile = payload.user_profile
        
        if not user_profile:
            return RecommendDepositSavingProductsResponse(
                success=False,
                user_profile=None,
                deposit_products=[],
                saving_products=[],
                error="user_profile이 제공되지 않았습니다.",
            )
        
        # Step 1: 검색 쿼리 생성
        search_query = _build_search_query_from_user(user_profile)
        logger.info(f"🔍 생성된 검색 쿼리: '{search_query}'")
        
        # Step 2: 임베딩 API 호출
        query_embedding = await _get_embeddings_from_api([search_query], normalize=True)
        
        logger.info(f"🔍 Query embedding shape: {query_embedding.shape}")
        logger.info(f"🔍 Query embedding dimension: {query_embedding.shape[1]}")
        
        # Step 3: 예금 상품 검색
        deposit_index, deposit_metadata = _load_plan_deposit_faiss()
        
        logger.info(f"🔍 Deposit FAISS index dimension: {deposit_index.d}")
        logger.info(f"🔍 Deposit FAISS total vectors: {deposit_index.ntotal}")
        
        # ✅ LangChain FAISS 메타데이터 구조 해석 (순서 수정!)
        if isinstance(deposit_metadata, tuple) and len(deposit_metadata) == 2:
            deposit_docstore, index_to_docstore_id = deposit_metadata  # ✅ 순서 변경!
            logger.info(f"✅ deposit_docstore 타입: {type(deposit_docstore)}")
            logger.info(f"✅ index_to_docstore_id 타입: {type(index_to_docstore_id)}")
        else:
            error_msg = f"예상치 못한 예금 메타데이터 구조: {type(deposit_metadata)}"
            logger.error(f"❌ {error_msg}")
            return RecommendDepositSavingProductsResponse(
                success=False,
                user_profile=user_profile,
                deposit_products=[],
                saving_products=[],
                error=error_msg,
            )
        
        # 차원 체크
        if query_embedding.shape[1] != deposit_index.d:
            error_msg = (
                f"예금 인덱스 차원 불일치: "
                f"Query={query_embedding.shape[1]}차원, "
                f"Index={deposit_index.d}차원"
            )
            logger.error(f"❌ {error_msg}")
            return RecommendDepositSavingProductsResponse(
                success=False,
                user_profile=user_profile,
                deposit_products=[],
                saving_products=[],
                error=error_msg,
            )
        
        # 예금 검색
        deposit_k = min(3, deposit_index.ntotal)
        deposit_products = []
        
        if deposit_k > 0:
            deposit_distances, deposit_indices = deposit_index.search(query_embedding, deposit_k)
            
            # ✅ docstore의 모든 문서를 리스트로 변환
            if hasattr(deposit_docstore, '_dict'):
                all_docs = list(deposit_docstore._dict.values())
                logger.info(f"🔍 Deposit docstore 문서 개수: {len(all_docs)}")
                logger.info(f"🔍 Deposit 검색 인덱스: {deposit_indices[0]}")
                logger.info(f"🔍 Deposit 검색 거리: {deposit_distances[0]}")
                
                for idx, distance in zip(deposit_indices[0], deposit_distances[0]):
                    try:
                        # ✅ index_to_docstore_id로 실제 doc_id 찾기
                        if index_to_docstore_id and idx in index_to_docstore_id:
                            doc_id = index_to_docstore_id[idx]
                            doc = deposit_docstore.search(doc_id)
                        elif idx < len(all_docs):
                            # fallback: 직접 인덱스로 접근
                            doc = all_docs[idx]
                        else:
                            logger.warning(f"❌ Index {idx} out of range")
                            continue
                        
                        if doc is None:
                            logger.warning(f"❌ Document at index {idx} is None")
                            continue
                        
                        logger.info(f"✅ 예금 문서 발견 (index={idx})")
                        
                        # Document 객체에서 정보 추출
                        product = {
                            "content": doc.page_content if hasattr(doc, 'page_content') else str(doc),
                            "similarity_score": float(1 / (1 + distance)),
                            "distance": float(distance),
                        }
                        
                        # metadata가 있으면 추가
                        if hasattr(doc, 'metadata') and doc.metadata:
                            product.update(doc.metadata)
                        
                        deposit_products.append(product)
                            
                    except Exception as e:
                        logger.error(f"❌ 예금 상품 추출 실패 (idx={idx}): {e}", exc_info=True)
                        continue
            else:
                logger.error("❌ deposit_docstore에 _dict 속성이 없습니다")
        
        logger.info(f"✅ 예금 상품 {len(deposit_products)}개 추출 완료")
        
        # Step 4: 적금 상품 검색
        saving_index, saving_metadata = _load_plan_saving_faiss()
        
        logger.info(f"🔍 Saving FAISS index dimension: {saving_index.d}")
        logger.info(f"🔍 Saving FAISS total vectors: {saving_index.ntotal}")
        
        # ✅ LangChain FAISS 메타데이터 구조 해석 (순서 수정!)
        if isinstance(saving_metadata, tuple) and len(saving_metadata) == 2:
            saving_docstore, index_to_docstore_id_saving = saving_metadata  # ✅ 순서 변경!
            logger.info(f"✅ saving_docstore 타입: {type(saving_docstore)}")
            logger.info(f"✅ index_to_docstore_id_saving 타입: {type(index_to_docstore_id_saving)}")
        else:
            error_msg = f"예상치 못한 적금 메타데이터 구조: {type(saving_metadata)}"
            logger.error(f"❌ {error_msg}")
            return RecommendDepositSavingProductsResponse(
                success=False,
                user_profile=user_profile,
                deposit_products=deposit_products,
                saving_products=[],
                error=error_msg,
            )
        
        # 차원 체크
        if query_embedding.shape[1] != saving_index.d:
            error_msg = f"적금 인덱스 차원 불일치: Query={query_embedding.shape[1]}차원, Index={saving_index.d}차원"
            logger.error(f"❌ {error_msg}")
            return RecommendDepositSavingProductsResponse(
                success=False,
                user_profile=user_profile,
                deposit_products=deposit_products,
                saving_products=[],
                error=error_msg,
            )
        
        # 적금 검색
        saving_k = min(3, saving_index.ntotal)
        saving_products = []
        
        if saving_k > 0:
            saving_distances, saving_indices = saving_index.search(query_embedding, saving_k)
            
            # ✅ docstore의 모든 문서를 리스트로 변환
            if hasattr(saving_docstore, '_dict'):
                all_docs = list(saving_docstore._dict.values())
                logger.info(f"🔍 Saving docstore 문서 개수: {len(all_docs)}")
                logger.info(f"🔍 Saving 검색 인덱스: {saving_indices[0]}")
                logger.info(f"🔍 Saving 검색 거리: {saving_distances[0]}")
                
                for idx, distance in zip(saving_indices[0], saving_distances[0]):
                    try:
                        # ✅ index_to_docstore_id로 실제 doc_id 찾기
                        if index_to_docstore_id_saving and idx in index_to_docstore_id_saving:
                            doc_id = index_to_docstore_id_saving[idx]
                            doc = saving_docstore.search(doc_id)
                        elif idx < len(all_docs):
                            # fallback: 직접 인덱스로 접근
                            doc = all_docs[idx]
                        else:
                            logger.warning(f"❌ Index {idx} out of range")
                            continue
                        
                        if doc is None:
                            logger.warning(f"❌ Document at index {idx} is None")
                            continue
                        
                        logger.info(f"✅ 적금 문서 발견 (index={idx})")
                        
                        # Document 객체에서 정보 추출
                        product = {
                            "content": doc.page_content if hasattr(doc, 'page_content') else str(doc),
                            "similarity_score": float(1 / (1 + distance)),
                            "distance": float(distance),
                        }
                        
                        # metadata가 있으면 추가
                        if hasattr(doc, 'metadata') and doc.metadata:
                            product.update(doc.metadata)
                        
                        saving_products.append(product)
                            
                    except Exception as e:
                        logger.error(f"❌ 적금 상품 추출 실패 (idx={idx}): {e}", exc_info=True)
                        continue
            else:
                logger.error("❌ saving_docstore에 _dict 속성이 없습니다")
        
        logger.info(f"✅ 적금 상품 {len(saving_products)}개 추출 완료")
        
        logger.info(
            f"✅ 추천 완료: 예금 {len(deposit_products)}개, 적금 {len(saving_products)}개"
        )
        
        return RecommendDepositSavingProductsResponse(
            success=True,
            user_profile=user_profile,
            deposit_products=deposit_products,
            saving_products=saving_products,
            meta={
                "search_query": search_query,
                "embedding_api": EMBEDDING_API_URL,
            },
        )
    
    except ConnectionError as e:
        logger.error(f"임베딩 API 연결 실패: {e}")
        return RecommendDepositSavingProductsResponse(
            success=False,
            user_profile=user_profile if 'user_profile' in locals() else None,
            deposit_products=[],
            saving_products=[],
            error=f"임베딩 서버 연결 실패: {str(e)}",
        )
    except Exception as e:
        logger.error(f"recommend_deposit_saving_products Error: {e}", exc_info=True)
        return RecommendDepositSavingProductsResponse(
            success=False,
            user_profile=user_profile if 'user_profile' in locals() else None,
            deposit_products=[],
            saving_products=[],
            error=f"추천 중 오류 발생: {str(e)}",
        )


# 8. 부족 자금(shortage_amount) 계산 Tool
@router.post(
    "/calc_shortage",
    summary="주택 자금 부족액 계산",
    operation_id="calc_shortage_amount",
    description=(
        "희망 주택 가격, 예상 대출 금액, 보유 자산을 입력받아 "
        "**부족 자금(Shortage Amount)** 을 계산합니다."
    ),
    response_model=CalcShortageAmountResponse,
)
async def calc_shortage_amount(
    payload: CalcShortageAmountRequest = Body(...),
) -> CalcShortageAmountResponse:
    """
    희망 주택 가격, 대출 금액, 보유 자산을 기반으로 부족 자금을 계산하는 Tool.
    (DB 업데이트 없음, 순수 계산 전용)
    """

    # 내부 유틸: 안전한 정수 변환
    def _to_int(v: Any) -> int:
        try:
            if v is None:
                return 0
            return int(float(v))
        except Exception:
            return 0

    try:
        hope_price = _to_int(payload.hope_price)
        loan_amount = _to_int(payload.loan_amount)
        initial_prop = _to_int(payload.initial_prop)

        shortage = max(0, hope_price - (loan_amount + initial_prop))

        return CalcShortageAmountResponse(
            success=True,
            shortage_amount=shortage,
            inputs={
                "hope_price": hope_price,
                "loan_amount": loan_amount,
                "initial_prop": initial_prop,
            },
            error=None,
        )
    except Exception as e:
        logger.error(f"calc_shortage_amount Error: {e}", exc_info=True)
        return CalcShortageAmountResponse(
            success=False,
            shortage_amount=0,
            inputs=None,
            error=str(e),
        )



# 10. 비율(예금/적금/펀드)에 따른 금액 계산 (스키마 기반)
@router.post(
    "/calculate_portfolio_amounts",
    summary="비율에 따른 금액 계산",
    operation_id="calculate_portfolio_amounts",
    response_model=CalculatePortfolioAmountsResponse,
)
async def api_calculate_portfolio_amounts(
    payload: CalculatePortfolioAmountsRequest = Body(...),
) -> CalculatePortfolioAmountsResponse:
    """
    총 자산과 비율(예: "30:40:30")을 입력받아
    예금/적금/펀드 각각의 금액을 계산합니다.
    """
    total_amount = payload.total_amount
    ratio_str = payload.ratio_str

    try:
        ratios = [int(n) for n in re.findall(r"\d+", ratio_str)]

        if len(ratios) != 3:
            return CalculatePortfolioAmountsResponse(
                success=False,
                amounts=None,
                error="비율은 예금:적금:펀드 3개 숫자로 입력해주세요.",
            )

        total_ratio = sum(ratios) or 1

        deposit_amt = int(total_amount * (ratios[0] / total_ratio))
        savings_amt = int(total_amount * (ratios[1] / total_ratio))
        fund_amt = int(total_amount * (ratios[2] / total_ratio))

        # 자투리 금액 보정 (펀드에 합산)
        diff = total_amount - (deposit_amt + savings_amt + fund_amt)
        fund_amt += diff

        return CalculatePortfolioAmountsResponse(
            success=True,
            amounts={
                "deposit": deposit_amt,
                "savings": savings_amt,
                "fund": fund_amt,
            },
            error=None,
        )
    except Exception as e:
        return CalculatePortfolioAmountsResponse(
            success=False,
            amounts=None,
            error=str(e),
        )


# 11. 사용자가 선택한 예금/적금 금액이 한도(deposit_amount, savings_amount)를 초과하는지 검증
@router.post(
    "/validate_selected_savings_products",
    summary="선택한 예금/적금 금액 검증",
    operation_id="validate_selected_savings_products",
    description=(
        "예금/적금 추천 후 사용자가 선택한 상품과 각 상품별 입력 금액이\n"
        "`/db/get_member_investment_amounts` Tool을 통해 조회한\n"
        "**예금/적금 배정 가능 한도**(members 테이블의 `deposite_amount`, `saving_amount` 기반)가\n"
        "초과되는지 검증합니다.\n\n"
        "출력:\n"
        "- success: 검증 성공 여부\n"
        "- total_selected_deposit / total_selected_savings: 선택 금액 총합\n"
        "- remaining_deposit_amount / remaining_savings_amount: 남은 한도(음수면 초과)\n"
        "- violations: 초과/유효성 관련 메시지 리스트"
    ),
    response_model=ValidateSelectedSavingsProductsResponse,
)
async def validate_selected_savings_products(
    payload: ValidateSelectedSavingsProductsRequest = Body(...),
) -> ValidateSelectedSavingsProductsResponse:
    """
    - deposit_amount: (프론트/에이전트 입장에서는) 예금 배정 가능 총액.
      실제 DB 컬럼은 members.deposite_amount 이며,
      값은 `/db/get_member_investment_amounts`에서 변환되어 들어온다고 가정.
    - savings_amount: 적금 배정 가능 총액 (DB 컬럼: members.saving_amount).
    - selected_deposits: [SelectedProductAmount, ...]
    - selected_savings: [SelectedProductAmount, ...]
    를 받아 한도 초과 여부를 검증.
    """

    def _to_int_safe(v: Any) -> int:
        try:
            if v is None or v == "":
                return 0
            return int(float(v))
        except Exception:
            return 0

    try:
        # 🔹 Pydantic 모델 필드 사용
        deposit_limit = _to_int_safe(payload.deposit_amount)
        savings_limit = _to_int_safe(payload.savings_amount)

        selected_deposits = payload.selected_deposits or []
        selected_savings = payload.selected_savings or []

        violations: List[str] = []

        # 개별 금액 음수/0 체크 및 총합 계산
        total_selected_deposit = 0
        for item in selected_deposits:
            name = item.product_name or "예금상품"
            amt = _to_int_safe(item.amount)
            if amt < 0:
                violations.append(
                    f"예금 상품 '{name}'의 금액이 음수입니다: {amt}원"
                )
            total_selected_deposit += max(0, amt)

        total_selected_savings = 0
        for item in selected_savings:
            name = item.product_name or "적금상품"
            amt = _to_int_safe(item.amount)
            if amt < 0:
                violations.append(
                    f"적금 상품 '{name}'의 금액이 음수입니다: {amt}원"
                )
            total_selected_savings += max(0, amt)

        remaining_deposit = deposit_limit - total_selected_deposit
        remaining_savings = savings_limit - total_selected_savings

        # 한도 음수/미설정 방어
        if deposit_limit < 0:
            violations.append(
                f"예금 한도(deposit_amount)가 0보다 작습니다: {deposit_limit}원"
            )
        if savings_limit < 0:
            violations.append(
                f"적금 한도(savings_amount)가 0보다 작습니다: {savings_limit}원"
            )

        # 한도 초과 체크
        if total_selected_deposit > deposit_limit:
            violations.append(
                f"선택한 예금 총액({total_selected_deposit:,}원)이 "
                f"예금 한도({deposit_limit:,}원)를 초과했습니다."
            )
        if total_selected_savings > savings_limit:
            violations.append(
                f"선택한 적금 총액({total_selected_savings:,}원)이 "
                f"적금 한도({savings_limit:,}원)를 초과했습니다."
            )

        success = len(violations) == 0

        return ValidateSelectedSavingsProductsResponse(
            success=success,
            deposit_amount=deposit_limit,
            savings_amount=savings_limit,
            total_selected_deposit=total_selected_deposit,
            total_selected_savings=total_selected_savings,
            remaining_deposit_amount=remaining_deposit,
            remaining_savings_amount=remaining_savings,
            violations=violations,
            error=None,
        )

    except Exception as e:
        logger.error(
            f"validate_selected_savings_products Error: {e}", exc_info=True
        )
        return ValidateSelectedSavingsProductsResponse(
            success=False,
            deposit_amount=payload.deposit_amount,
            savings_amount=payload.savings_amount,
            total_selected_deposit=0,
            total_selected_savings=0,
            remaining_deposit_amount=0,
            remaining_savings_amount=0,
            violations=[],
            error=str(e),
        )


@router.post(
    "/validate_selected_funds_products",
    summary="선택 펀드 금액 검증",
    operation_id="validate_selected_funds_products",
    description=(
        "펀드 추천 후 사용자가 선택한 펀드들의 총합이\n"
        "`/db/get_member_investment_amounts` Tool로 조회한 "
        "**펀드 배정 가능 한도**(members.fund_amount 기반)를 초과하는지 검증합니다."
    ),
    response_model=ValidateSelectedFundsProductsResponse,
)
async def validate_selected_funds_products(
    payload: ValidateSelectedFundsProductsRequest = Body(...),
) -> ValidateSelectedFundsProductsResponse:
    """
    - fund_amount: 펀드 배정 가능 총액 (실제 DB 컬럼: members.fund_amount).
      값은 `/db/get_member_investment_amounts` Tool을 통해 미리 조회되어 들어온다고 가정.
    - selected_funds: [SelectedFundAmount, ...]
    """
    try:
        fund_limit = int(payload.fund_amount or 0)
        
        # selected_funds가 Dict 리스트이므로 딕셔너리로 접근
        total_selected = 0
        for fund in payload.selected_funds:
            amount = fund.get("amount", 0)
            total_selected += int(amount or 0)

        remaining = fund_limit - total_selected
        violations: List[str] = []

        if total_selected <= 0:
            violations.append(
                "선택한 펀드 금액이 0원입니다. 최소 1원 이상 입력해 주세요."
            )

        if total_selected > fund_limit:
            violations.append(
                f"선택한 펀드 총액({total_selected:,}원)가 "
                f"펀드 한도({fund_limit:,}원)를 초과합니다."
            )

        success = len(violations) == 0

        return ValidateSelectedFundsProductsResponse(
            success=success,
            fund_amount=fund_limit,
            total_selected_fund=total_selected,
            remaining_fund_amount=remaining,
            violations=violations,
            error=None,
        )
    except Exception as e:
        logger.error(
            f"validate_selected_funds_products Error: {e}", exc_info=True
        )
        return ValidateSelectedFundsProductsResponse(
            success=False,
            fund_amount=0,
            total_selected_fund=0,
            remaining_fund_amount=0,
            violations=[],
            error=str(e),
        )

# ============================================================
# 주택담보대출 TOOLS
# ============================================================

@router.post(
    "/calculate_ltv",
    summary="LTV(담보인정비율) 계산",
    operation_id="calculate_ltv",
    description=(
        "사용자 정보와 주택 정보를 바탕으로 LTV 비율을 계산합니다.\n\n"
        "**고려사항:**\n"
        "- 주택 유형별 기본 LTV (아파트 70%, 오피스텔/연립다세대 60%, 단독다가구 50%)\n"
        "- 가격 구간별 조정 (6억 초과 -5%p, 9억 초과 -10%p)\n"
        "- 규제지역 여부 (-10%p)\n"
        "- 사용자 신용점수 (700 미만 -5%p, 800 이상 +5%p)\n"
        "- 기존 대출 개수 (2건 이상 -5%p)\n"
        "- 생애최초 주택 구매 여부 (+5%p)"
    ),
    response_model=CalculateLTVResponse,
)
async def api_calculate_ltv(
    request: CalculateLTVRequest = Body(...),
):
    """LTV(Loan To Value) 비율 계산"""
    try:
        # ✅ 안전한 타입 변환 헬퍼 함수
        def _safe_int(v, default: int = 0) -> int:
            """None, 문자열 'None', 빈 문자열 등을 안전하게 int로 변환"""
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return int(v)
            s = str(v).strip().lower()
            if s in ('none', '', 'null', 'nan'):
                return default
            try:
                return int(float(s))
            except (ValueError, TypeError):
                return default
        
        def _safe_str(v, default: str = "") -> str:
            """None, 문자열 'None' 등을 안전하게 문자열로 변환"""
            if v is None:
                return default
            s = str(v).strip()
            if s.lower() in ('none', 'null'):
                return default
            return s
        
        # # DB 연결
        # db_url = f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
        # engine = create_engine(db_url)
        
        with engine.connect() as conn:
            # 1. 사용자 기본 정보 조회
            user_query = text("""
                SELECT 
                    m.hope_housing_type,
                    m.hope_location,
                    m.existing_loans,
                    mi.credit_score,
                    mi.loan_count,
                    mi.first_home_buyer,
                    mi.has_house
                FROM members m
                LEFT JOIN members_info mi ON m.user_id = mi.user_id
                WHERE m.user_id = :user_id
                ORDER BY mi.year_month DESC
                LIMIT 1
            """)
            
            user_row = conn.execute(user_query, {"user_id": request.user_id}).fetchone()
            
            if not user_row:
                return CalculateLTVResponse(
                    success=False,
                    error="사용자 정보를 찾을 수 없습니다"
                )
            
            # ✅ 안전한 변환 사용
            hope_housing_type = _safe_str(user_row[0], "아파트")
            hope_location = _safe_str(user_row[1], "")
            existing_loans = _safe_int(user_row[2], 0)
            credit_score = _safe_int(user_row[3], 700)
            loan_count = _safe_int(user_row[4], 0)
            first_home_buyer = _safe_int(user_row[5], 0)
            has_house = _safe_int(user_row[6], 0)
            
            logger.info(f"📊 사용자 정보: housing={hope_housing_type}, location={hope_location}, "
                       f"existing_loans={existing_loans}, credit={credit_score}, "
                       f"loan_count={loan_count}, first_home={first_home_buyer}, has_house={has_house}")
            
            # 2. 지역 평균 가격 조회
            regional_avg_price = 0
            if hope_location:
                region_query = text("""
                    SELECT 
                        apartment_price,
                        multi_price,
                        officetel_price,
                        detached_price
                    FROM state
                    WHERE region_nm LIKE :location
                    LIMIT 1
                """)
                
                region_row = conn.execute(
                    region_query,
                    {"location": f"%{hope_location}%"}
                ).fetchone()
                
                if region_row:
                    if hope_housing_type == "아파트":
                        regional_avg_price = _safe_int(region_row[0], 0)
                    elif hope_housing_type == "연립다세대":
                        regional_avg_price = _safe_int(region_row[1], 0)
                    elif hope_housing_type == "오피스텔":
                        regional_avg_price = _safe_int(region_row[2], 0)
                    elif hope_housing_type == "단독다가구":
                        regional_avg_price = _safe_int(region_row[3], 0)
            
            # 3. 기본 LTV 비율 설정
            base_ltv_map = {
                "아파트": 70.0,
                "연립다세대": 60.0,
                "오피스텔": 60.0,
                "단독다가구": 50.0
            }
            
            ltv_ratio = base_ltv_map.get(hope_housing_type, 60.0)
            reason_parts = [f"{hope_housing_type} 기본 {ltv_ratio}%"]
            
            # 4. 가격 구간별 조정
            target_price = _safe_int(request.target_price, 0)
            if target_price > 900000000:
                ltv_ratio -= 10.0
                reason_parts.append("9억 초과 -10%p")
            elif target_price > 600000000:
                ltv_ratio -= 5.0
                reason_parts.append("6억 초과 -5%p")
            
            # 5. 규제지역 조정
            if request.is_regulated_area:
                ltv_ratio -= 10.0
                reason_parts.append("규제지역 -10%p")
            
            # 6. 신용점수 조정
            if credit_score < 700:
                ltv_ratio -= 5.0
                reason_parts.append(f"신용점수 {credit_score}점 -5%p")
            elif credit_score >= 800:
                ltv_ratio += 5.0
                reason_parts.append(f"신용점수 {credit_score}점 +5%p")
            
            # 7. 기존 대출 조정
            total_loans = max(existing_loans, loan_count)
            if total_loans >= 2:
                ltv_ratio -= 5.0
                reason_parts.append(f"기존 대출 {total_loans}건 -5%p")
            
            # 8. 2주택자 페널티 (중요!)
            if has_house == 1:
                ltv_ratio -= 50.0  # 2주택자는 LTV가 대폭 감소
                reason_parts.append("2주택자 -50%p")
            
            # 9. 생애 최초 주택 구매자 우대
            if first_home_buyer == 1:
                ltv_ratio += 5.0
                reason_parts.append("생애최초 +5%p")
            
            # 최소/최대 LTV 제한
            ltv_ratio = max(30.0, min(ltv_ratio, 80.0))
            
            # 최대 대출 금액 계산
            max_loan_amount = int(target_price * (ltv_ratio / 100))
            
            logger.info(f"✅ LTV 계산 완료: {ltv_ratio}%, 최대 {max_loan_amount:,}원")
            
            return CalculateLTVResponse(
                success=True,
                ltv_ratio=ltv_ratio,
                max_loan_amount=max_loan_amount,
                reason=" / ".join(reason_parts),
                regional_avg_price=regional_avg_price
            )
            
    except Exception as e:
        logger.error(f"❌ LTV 계산 실패: {e}", exc_info=True)
        return CalculateLTVResponse(
            success=False,
            error=f"LTV 계산 실패: {str(e)}"
        )


@router.post(
    "/get_loan_product",
    summary="주택담보대출 상품 조회",
    operation_id="get_loan_product",
    description=(
        "DB에서 주택담보대출 상품 정보를 조회합니다.\n"
        "product_id가 없으면 첫 번째 주택담보대출 상품을 반환합니다."
    ),
    response_model=GetLoanProductResponse,
)
async def api_get_loan_product(
    request: GetLoanProductRequest = Body(
        ...,
        description="대출 상품 조회 요청",
    )
):
    """
    주택담보대출 상품 조회
    
    DB에서 주택담보대출 상품 정보를 조회합니다.
    product_id가 없으면 첫 번째 주택담보대출 상품을 반환합니다.
    """
    try:
        # db_url = f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
        # engine = create_engine(db_url)
        
        with engine.connect() as conn:
            if request.product_id:
                # product_id가 지정된 경우 해당 상품 조회
                query = text("""
                    SELECT 
                        product_id, product_name, bank_name, product_type,
                        summary, target_housing_type, rate_description,
                        repayment_method, preferential_rate_info
                    FROM loan_product
                    WHERE product_id = :product_id
                    LIMIT 1
                """)
                row = conn.execute(query, {"product_id": request.product_id}).fetchone()
            else:
                # product_id가 없으면 첫 번째 상품 조회 (product_type 필터 제거)
                query = text("""
                    SELECT 
                        product_id, product_name, bank_name, product_type,
                        summary, target_housing_type, rate_description,
                        repayment_method, preferential_rate_info
                    FROM loan_product
                    LIMIT 1
                """)
                row = conn.execute(query).fetchone()
            
            if not row:
                return GetLoanProductResponse(
                    success=False,
                    error="주택담보대출 상품을 찾을 수 없습니다. loan_product 테이블에 데이터가 없습니다."
                )
            
            logger.info(f"✅ 대출 상품 조회 완료: {row[1]}")
            
            return GetLoanProductResponse(
                success=True,
                product_id=row[0],
                product_name=row[1],
                bank_name=row[2],
                product_type=row[3],
                summary=row[4],
                target_housing_type=row[5],
                rate_description=row[6],
                repayment_method=row[7],
                preferential_rate_info=row[8]
            )
            
    except Exception as e:
        logger.error(f"❌ 대출 상품 조회 실패: {e}", exc_info=True)
        return GetLoanProductResponse(
            success=False,
            error=f"대출 상품 조회 실패: {str(e)}"
        )


@router.post(
    "/calculate_final_loan_simple",
    summary="최종 대출 가능 금액 산정",
    operation_id="calculate_final_loan_simple",
    description=(
        "LTV, DSR, DTI를 종합적으로 고려하여 최종 대출 가능 금액을 계산합니다."
    ),
    response_model=CalculateFinalLoanResponse,
)
# 대출금 40% 고정해서 받는 버전
async def api_calculate_final_loan_simple(
    request: CalculateFinalLoanRequest = Body(..., description="최종 대출 금액 산정")
):
    """
    최종 대출 금액 산정 - 간단 버전
    
    희망 주택가격의 40%를 대출금액으로 산정
    """
    try:
        with engine.connect() as conn:
            # 사용자 초기 자본 조회
            user_query = text("""
                SELECT initial_prop, is_loan_possible
                FROM members
                WHERE user_id = :user_id
            """)
            
            user_row = conn.execute(user_query, {"user_id": request.user_id}).fetchone()
            
            if not user_row:
                return CalculateFinalLoanResponse(
                    success=False,
                    error="사용자 정보를 찾을 수 없습니다"
                )
            
            initial_prop = user_row[0] or 0
            is_loan_possible = user_row[1]
            
            if is_loan_possible == 0:
                return CalculateFinalLoanResponse(
                    success=False,
                    error="대출 불가능 상태입니다"
                )
            
            # 대출 금액 = 희망 주택가격 × 40%
            approved_amount = int(request.target_price * 0.4)
            down_payment_needed = request.target_price - approved_amount
            
            if down_payment_needed > initial_prop:
                shortage = down_payment_needed - initial_prop
                return CalculateFinalLoanResponse(
                    success=False,
                    approved_amount=approved_amount,
                    shortage_amount=shortage,
                    down_payment_needed=down_payment_needed,
                    error=f"자기자본 {shortage:,}원 부족"
                )
            
            logger.info(f"✅ 간단 대출 산정: {approved_amount:,}원 (40% 고정)")
            
            return CalculateFinalLoanResponse(
                success=True,
                approved_amount=approved_amount,
                down_payment_needed=down_payment_needed
            )
            
    except Exception as e:
        logger.error(f"❌ 대출 계산 실패: {e}", exc_info=True)
        return CalculateFinalLoanResponse(
            success=False,
            error=f"대출 계산 실패: {str(e)}"
        )
# ============================================================
# Summary Agent MCP Tools
# ============================================================

# simulate_investment(투자 시물레이션)
# 복리 기반 투자 시뮬레이션 Tool
@router.post(
    "/simulate_investment",
    summary="복리 기반 투자 시뮬레이션",
    operation_id="simulate_investment",
    description=(
        "부족 자금을 채우기 위한 **예금/적금 + 펀드** 복합 투자 시뮬레이션을 수행합니다."
    ),
    response_model=SimulateInvestmentResponse,
)
async def simulate_investment(
    payload: SimulateInvestmentRequest = Body(...),
) -> SimulateInvestmentResponse:
    """
    예금/적금 + 펀드 복합 투자를 단순 월복리 모델로 시뮬레이션하는 Tool.
    (DB / LLM 사용 없음)
    """

    # 내부 유틸: 숫자 변환
    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _to_int(v: Any, default: int = 0) -> int:
        try:
            if v is None:
                return default
            return int(float(v))
        except Exception:
            return default

    # 내부 유틸: 시뮬레이션 로직
    def _simulate(
        shortage: int,
        available_assets: int,
        monthly_income: float,
        income_usage_ratio: float,
        saving_yield: float,
        fund_yield: float,
        saving_ratio: float,
        fund_ratio: float,
    ) -> Dict[str, Any]:
        # 이미 부족금이 없으면 바로 종료
        if shortage <= 0:
            return {
                "months_needed": 0,
                "total_balance": available_assets,
                "monthly_invest": int(
                    monthly_income * (income_usage_ratio / 100)
                ),
                "saving_ratio": saving_ratio,
                "fund_ratio": fund_ratio,
            }

        # 초기 자산 분배
        init_saving = available_assets * saving_ratio
        init_fund = available_assets * fund_ratio

        monthly_invest = monthly_income * (income_usage_ratio / 100.0)
        saving_monthly = monthly_invest * saving_ratio
        fund_monthly = monthly_invest * fund_ratio

        total_balance = init_saving + init_fund
        months = 0

        # 최대 600개월(50년) 제한
        while total_balance < shortage and months < 600:
            months += 1
            # 월복리 적용 (연 수익률 -> 월 수익률 = r/12)
            init_saving = (init_saving + saving_monthly) * (
                1 + saving_yield / 100.0 / 12.0
            )
            init_fund = (init_fund + fund_monthly) * (
                1 + fund_yield / 100.0 / 12.0
            )
            total_balance = init_saving + init_fund

        return {
            "months_needed": months,
            "total_balance": int(total_balance),
            "monthly_invest": int(monthly_invest),
            "saving_ratio": saving_ratio,
            "fund_ratio": fund_ratio,
        }

    try:
        shortage = _to_int(payload.shortage, 0)
        available_assets = _to_int(payload.available_assets, 0)
        monthly_income = _to_float(payload.monthly_income, 0.0)
        income_usage_ratio = _to_float(payload.income_usage_ratio, 20.0)

        saving_yield = _to_float(payload.saving_yield, 3.0)
        fund_yield = _to_float(payload.fund_yield, 6.0)

        saving_ratio = _to_float(payload.saving_ratio, 0.5)
        fund_ratio = _to_float(payload.fund_ratio, 0.5)

        simulation = _simulate(
            shortage=shortage,
            available_assets=available_assets,
            monthly_income=monthly_income,
            income_usage_ratio=income_usage_ratio,
            saving_yield=saving_yield,
            fund_yield=fund_yield,
            saving_ratio=saving_ratio,
            fund_ratio=fund_ratio,
        )

        return SimulateInvestmentResponse(
            success=True,
            simulation=simulation,
            inputs={
                "shortage": shortage,
                "available_assets": available_assets,
                "monthly_income": monthly_income,
                "income_usage_ratio": income_usage_ratio,
                "saving_yield": saving_yield,
                "fund_yield": fund_yield,
                "saving_ratio": saving_ratio,
                "fund_ratio": fund_ratio,
            },
            error=None,
        )

    except Exception as e:
        logger.error(f"simulate_investment Error: {e}", exc_info=True)
        return SimulateInvestmentResponse(
            success=False,
            simulation=None,
            inputs=None,
            error=str(e),
        )