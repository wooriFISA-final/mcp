import json
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
from langchain_huggingface import HuggingFaceEndpointEmbeddings

from langchain_ollama import OllamaEmbeddings

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
    SelectTopFundsByRiskRequest,
    SelectTopFundsByRiskResponse,
    CalcShortageAmountRequest,
    CalcShortageAmountResponse,
    SimulateInvestmentRequest,
    SimulateInvestmentResponse,
    GetSavingsCandidatesRequest,
    GetSavingsCandidatesResponse,
    RecommendSavingsProductsRequest,
    RecommendSavingsProductsResponse,
    CheckPlanCompletionRequest,
    CheckPlanCompletionResponse,
    ValidateSelectedSavingsProductsRequest,
    ValidateSelectedSavingsProductsResponse,
    ValidateSelectedFundsProductsRequest,
    ValidateSelectedFundsProductsResponse,
    CalculatePortfolioAmountsRequest,
    CalculatePortfolioAmountsResponse,
)

# 라우터 설정
router = APIRouter(
    prefix="/input",  # API 엔드포인트 기본 경로
    tags=["PlanInput & Validation Tools"],  # Swagger UI용 카테고리 표시
)

logger = logging.getLogger(__name__)

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
_embeddings: Optional[OllamaEmbeddings] = None


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
        "입력 필드 예시:\n"
        "- data.initial_prop: 초기 자산 (예: '3천만', 30000000)\n"
        "- data.hope_location: 희망 지역 (예: '서울 동작구')\n"
        "- data.hope_price: 희망 가격 (예: '7억', 700000000)\n"
        "- data.hope_housing_type: 주택 유형 (예: '아파트')\n"
        "- data.income_usage_ratio: 월급 사용 비율 (예: '30%', 30)\n\n"
        "출력 필드:\n"
        "- status: 'success' | 'incomplete' | 'error'\n"
        "- data: 정규화된 결과 (success일 때)\n"
        "- missing_fields: 누락된 필드 목록 (incomplete일 때)"
    ),
    response_model=ValidateInputResponse,
)
async def validate_input_data(
    payload: ValidateInputRequest = Body(...),
) -> ValidateInputResponse:
    """
    전체 입력 데이터의 누락 필드를 검사하고,
    금액·비율·지역 정보를 표준화하여 반환.
    """
    try:
        data = payload.data
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
async def filter_top_savings_products(
    payload: Dict[str, Any] = Body(...),
) -> dict:
    """
    예·적금 CSV에서 사용자 조건에 맞는 상품을 필터링하고,
    예금/적금 각각 max_rate 기준 Top3를 반환하는 Tool.
    """
    try:
        user_data: Dict[str, Any] = payload.get("user_data", {}) or {}
        csv_path: str = payload.get("csv_file_path") or ""

        # 1) CSV 경로 설정 (없거나 존재하지 않으면 기본값 사용)
        if not csv_path or not os.path.exists(csv_path):
            logger.warning(
                "csv_file_path가 전달되지 않았거나 존재하지 않아 기본 경로를 사용합니다. "
                f"(입력값: {csv_path})"
            )
            default_path = (
                Path(__file__).resolve().parents[2] / "data" / "saving_data.csv"
            )
            csv_path = str(default_path)

        if not os.path.exists(csv_path):
            msg = f"CSV 파일을 찾을 수 없습니다: {csv_path}"
            logger.error(msg)
            return {
                "tool_name": "filter_top_savings_products",
                "success": False,
                "error": msg,
                "top_deposits": [],
                "top_savings": [],
            }

        # 2) CSV 로드
        try:
            all_products_df = pd.read_csv(csv_path)
        except Exception as e:
            msg = f"CSV 로드 실패 ({csv_path}): {e}"
            logger.error(msg)
            return {
                "tool_name": "filter_top_savings_products",
                "success": False,
                "error": msg,
                "top_deposits": [],
                "top_savings": [],
            }

        # 3) 공통 필터 기준
        age = int(user_data.get("age", 0) or 0)
        is_first_customer = bool(user_data.get("is_first_customer", True))
        period = int(user_data.get("period_goal_months", 12) or 12)

        # ============================
        # 3-1) 예금 필터링
        # ============================
        try:
            deposits_df = all_products_df[
                all_products_df["product_type"] == "예금"
            ].copy()

            # 나이 조건
            if "condition_min_age" in deposits_df.columns:
                deposits_df = deposits_df[deposits_df["condition_min_age"] <= age]

            # 첫거래 조건
            if (
                "condition_first_customer" in deposits_df.columns
                and not is_first_customer
            ):
                deposits_df = deposits_df[
                    deposits_df["condition_first_customer"] == False
                ]

            # 기간 조건
            if {"min_term", "max_term"}.issubset(deposits_df.columns):
                deposits_df = deposits_df[
                    (deposits_df["min_term"] <= period)
                    & (deposits_df["max_term"] >= period)
                ]

            # 금리 기준 Top3
            if "max_rate" in deposits_df.columns:
                deposits_df = deposits_df.sort_values(
                    by="max_rate", ascending=False
                )

            top_3_deposits = deposits_df.head(3)
            top_deposits = top_3_deposits.to_dict(orient="records")
        except Exception as e:
            logger.error(f"예금 필터링 중 오류: {e}")
            top_deposits = []

        # ============================
        # 3-2) 적금 필터링
        # ============================
        try:
            savings_df = all_products_df[
                all_products_df["product_type"] == "적금"
            ].copy()

            # 나이 조건
            if "condition_min_age" in savings_df.columns:
                savings_df = savings_df[savings_df["condition_min_age"] <= age]

            # 첫거래 조건
            if (
                "condition_first_customer" in savings_df.columns
                and not is_first_customer
            ):
                savings_df = savings_df[
                    savings_df["condition_first_customer"] == False
                ]

            # 기간 조건
            if {"min_term", "max_term"}.issubset(savings_df.columns):
                savings_df = savings_df[
                    (savings_df["min_term"] <= period)
                    & (savings_df["max_term"] >= period)
                ]

            # 금리 기준 Top3
            if "max_rate" in savings_df.columns:
                savings_df = savings_df.sort_values(
                    by="max_rate", ascending=False
                )

            top_3_savings = savings_df.head(3)
            top_savings = top_3_savings.to_dict(orient="records")
        except Exception as e:
            logger.error(f"적금 필터링 중 오류: {e}")
            top_savings = []

        return {
            "tool_name": "filter_top_savings_products",
            "success": True,
            "top_deposits": top_deposits,
            "top_savings": top_savings,
            "meta": {
                "csv_path": csv_path,
                "user_data": user_data,
                "count_deposits": len(top_deposits),
                "count_savings": len(top_savings),
            },
        }

    except Exception as e:
        logger.error(f"filter_top_savings_products Error: {e}", exc_info=True)
        return {
            "tool_name": "filter_top_savings_products",
            "success": False,
            "error": str(e),
            "top_deposits": [],
            "top_savings": [],
        }


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


# 9. 복리 기반 투자 시뮬레이션 Tool
@router.post(
    "/simulate_investment",
    summary="복리 기반 투자 시뮬레이션",
    operation_id="simulate_combined_investment",
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
        total_selected = sum(int(f.amount or 0) for f in payload.selected_funds)

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
