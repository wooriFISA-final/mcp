# schemas/plan_agent_tools.py

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Literal


# ============================================================
# 1) parse_currency -------------------------------------------
# ============================================================

class ParseCurrencyRequest(BaseModel):
    value: Any = Field(
        ...,
        description="한국어 금액 문자열 또는 숫자 금액 파싱",
    )


class ParseCurrencyResponse(BaseModel):
    tool_name: str = Field(
        "parse_currency",
        description="원 단위 정수 반환",
    )
    success: bool = Field(..., description="처리 성공 여부")
    parsed: int = Field(..., description="원 단위 정수 변환 결과")
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 2) health ---------------------------------------------------
# ============================================================

class HealthResponse(BaseModel):
    tool_name: str = Field(
        "plan_health",
        description="에이전트 상태 점검",
    )
    success: bool = Field(..., description="헬스 체크 성공 여부")
    llm_model: Optional[str] = Field(
        None,
        description="사용 중인 LLM 모델명",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 3) normalize_location ---------------------------------------
# ============================================================

class NormalizeLocationRequest(BaseModel):
    location: str = Field(
        ...,
        description="지역명 정규화",
    )


class NormalizeLocationResponse(BaseModel):
    tool_name: str = Field(
        "normalize_location",
        description="지역명 정규화",
    )
    success: bool = Field(..., description="정규화 성공 여부")
    normalized: str = Field(
        ...,
        description="정규화된 표준 행정구역명",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 4) parse_ratio ----------------------------------------------
# ============================================================

class ParseRatioRequest(BaseModel):
    value: str = Field(
        ...,
        description="비율 파싱",
    )


class ParseRatioResponse(BaseModel):
    tool_name: str = Field(
        "parse_ratio",
        description="비율 파싱",
    )
    success: bool = Field(..., description="처리 성공 여부")
    ratio: int = Field(..., description="정수 비율 값")
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 5) validate_input_data --------------------------------------
# ============================================================

class ValidateInputRequest(BaseModel):
    """
    LLM/클라이언트가 보내는 원시(raw) 입력 데이터.
    기존에는 ValidateInputRaw를 중첩했지만, 간단히 Dict로 평탄화.
    예시:
    {
      "data": {
        "initial_prop": "3억",
        "hope_location": "서울 동작구",
        "hope_price": "10억",
        "hope_housing_type": "아파트",
        "income_usage_ratio": "30%"
      }
    }
    """
    data: Dict[str, Any] = Field(
        ...,
        description="주택 계획 입력값",
    )


class ValidateInputResponse(BaseModel):
    tool_name: str = Field(
        "validate_input_data",
        description="사용자 입력값 검증",
    )
    success: bool = Field(..., description="전체 처리 성공 여부")
    status: Literal["success", "incomplete", "error"] = Field(
        ...,
        description="결과 상태",
    )
    # 정규화된 결과를 자유 형식 딕셔너리로 (초기_prop, hope_price 등)
    data: Optional[Dict[str, Any]] = Field(
        None,
        description="정규화된 입력값 데이터 (initial_prop, hope_price 등)",
    )
    missing_fields: List[str] = Field(
        default_factory=list,
        description="누락된 필드 목록",
    )
    message: Optional[str] = Field(
        None,
        description="에러 또는 추가 설명 메시지",
    )


# ============================================================
# 7) select_top_funds_by_risk ---------------------------------
# ============================================================

class SelectTopFundsByRiskRequest(BaseModel):
    fund_data: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="펀드 데이터 리스트",
    )
    fund_data_path: Optional[str] = Field(
        None,
        description=(
            "펀드 데이터 JSON 파일 경로. "
            "fund_data가 없고 경로가 없으면 기본 경로(fund_data.json)를 사용합니다."
        ),
    )


class SelectTopFundsByRiskResponse(BaseModel):
    tool_name: str = Field(
        "select_top_funds_by_risk",
        description="리스크 레벨별 Top1 펀드 선택",
    )
    success: bool = Field(..., description="처리 성공 여부")
    # 각 원소는 {risk_level, product_name, expected_return, description, ...} 형태 딕셔너리
    recommendations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="리스크 레벨별 Top1 펀드 목록",
    )
    # total_input_funds, unique_risk_levels, source, fund_data_path 등 메타정보를 담는 딕셔너리
    meta: Optional[Dict[str, Any]] = Field(
        None,
        description="부가 정보",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 8) calc_shortage_amount -------------------------------------
# ============================================================

class CalcShortageAmountRequest(BaseModel):
    hope_price: Any = Field(
        ...,
        description="희망 주택 가격",
    )
    loan_amount: Any = Field(
        ...,
        description="예상 대출 금액",
    )
    initial_prop: Any = Field(
        ...,
        description="보유 자산",
    )


class CalcShortageAmountResponse(BaseModel):
    tool_name: str = Field(
        "calc_shortage_amount",
        description="남은 금액 계산",
    )
    success: bool = Field(..., description="처리 성공 여부")
    shortage_amount: int = Field(
        ...,
        description="계산된 부족 자금",
    )
    # hope_price, loan_amount, initial_prop 정수화 결과를 key-value로 담는 딕셔너리
    inputs: Optional[Dict[str, int]] = Field(
        None,
        description="계산에 사용된 입력값",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 9) simulate_investment --------------------------------------
# ============================================================

class SimulateInvestmentRequest(BaseModel):
    shortage: Any = Field(
        ...,
        description="채워야 할 부족 금액",
    )
    available_assets: Any = Field(
        ...,
        description="현재 투자에 투입 가능한 자산",
    )
    monthly_income: Any = Field(
        ...,
        description="월 소득",
    )
    income_usage_ratio: Any = Field(
        ...,
        description="월 소득 중 투자에 사용할 비율",
    )
    saving_yield: Any = Field(
        ...,
        description="예금/적금 연 수익률",
    )
    fund_yield: Any = Field(
        ...,
        description="펀드 연 수익률",
    )
    saving_ratio: Any = Field(
        ...,
        description="투자 비중 중 예금/적금 비율",
    )
    fund_ratio: Any = Field(
        ...,
        description="투자 비중 중 펀드 비율",
    )


class SimulateInvestmentResponse(BaseModel):
    tool_name: str = Field(
        "simulate_combined_investment",
        description="투자 시뮬레이션",
    )
    success: bool = Field(..., description="처리 성공 여부")
    # months_needed, total_balance, monthly_invest, saving_ratio, fund_ratio 등을 담는 딕셔너리
    simulation: Optional[Dict[str, Any]] = Field(
        None,
        description="시뮬레이션 결과",
    )
    # shortage, available_assets, monthly_income, income_usage_ratio, saving_yield, fund_yield, saving_ratio, fund_ratio
    inputs: Optional[Dict[str, Any]] = Field(
        None,
        description="시뮬레이션에 사용된 입력값",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# DB관련 TOOLS SCHEMA
# ============================================================
# 1) /db/get_market_price -------------------------------------
# ============================================================
class GetMarketPriceRequest(BaseModel):
    location: str = Field(..., description="지역명 (예: '서울특별시 마포구')")
    housing_type: Literal["아파트", "오피스텔", "연립다세대", "단독다가구"] = Field(
        ...,
        description="주택유형",
    )


class GetMarketPriceResponse(BaseModel):
    tool_name: Literal["get_market_price"] = Field(
        "get_market_price",
        description="Tool 이름",
    )
    success: bool = Field(..., description="조회 성공 여부")
    avg_price: int = Field(..., description="평균 시세(원 단위, 없으면 0)")
    error: Optional[str] = Field(
        None,
        description="오류 메시지(실패 시)",
    )


# ============================================================
# 2) /db/upsert_member_and_plan ------------------------------
# ============================================================
class UpsertMemberAndPlanRequest(BaseModel):
    user_id: Optional[int] = Field(
        1,
        description="사용자 ID (없으면 기본값 1)",
    )
    initial_prop: int = Field(..., description="초기 자산 (원 단위)")
    hope_location: str = Field(..., description="희망 지역명 (예: '서울특별시 마포구')")
    hope_price: int = Field(..., description="희망 주택 가격 (원 단위)")
    hope_housing_type: str = Field(
        "아파트",
        description="주택 유형 (예: '아파트')",
    )
    income_usage_ratio: int = Field(
        ...,
        description="소득 중 주택 자금에 사용할 비율(%)",
    )


class UpsertMemberAndPlanResponse(BaseModel):
    tool_name: Literal["upsert_member_and_plan"] = Field(
        "upsert_member_and_plan",
        description="멤버 테이블과 플랜 테이블 데이터 삽입",
    )
    success: bool = Field(..., description="처리 성공 여부")
    user_id: int = Field(..., description="처리된 사용자 ID")
    error: Optional[str] = Field(
        None,
        description="오류 메시지(실패 시)",
    )


# ============================================================
# 3) /db/update_loan_result -----------------------------------
# ============================================================
class UpdateLoanResultRequest(BaseModel):
    user_id: Optional[int] = Field(1, description="사용자 ID")
    loan_amount: int = Field(..., description="최종 대출 금액(원)")
    shortage_amount: int = Field(..., description="부족 자금(원)")
    product_id: int = Field(..., description="대출 상품 ID")
    dsr: Optional[float] = Field(
        None,
        description="적용된 DSR 비율(%)",
    )
    dti: Optional[float] = Field(
        None,
        description="적용된 DTI 비율(%)",
    )


class UpdateLoanResultResponse(BaseModel):
    tool_name: Literal["update_loan_result"] = Field(
        "update_loan_result",
        description="대출 결과",
    )
    success: bool = Field(..., description="처리 성공 여부")
    user_id: int = Field(..., description="사용자 ID")
    updated_plan_id: Optional[int] = Field(
        None,
        description="대출 정보가 반영된 plan_id",
    )
    dsr: Optional[float] = Field(None, description="적용된 DSR(%)")
    dti: Optional[float] = Field(None, description="적용된 DTI(%)")
    error: Optional[str] = Field(None, description="오류 메시지(실패 시)")


# ============================================================
# 4) /db/get_user_loan_overview ------------------------------
# ============================================================
class GetUserLoanOverviewRequest(BaseModel):
    user_id: Optional[int] = Field(1, description="사용자 ID")


class GetUserLoanOverviewResponse(BaseModel):
    tool_name: Literal["get_user_loan_overview"] = Field(
        "get_user_loan_overview",
        description="세 테이블 조인",
    )
    success: bool = Field(..., description="조회 성공 여부")
    user_loan_info: Optional[Dict[str, Any]] = Field(
        None,
        description="members + plans + loan_product JOIN 결과",
    )
    error: Optional[str] = Field(None, description="오류 메시지")


# ============================================================
# 5) /db/update_shortage_amount -------------------------------
# ============================================================
class UpdateShortageAmountRequest(BaseModel):
    user_id: Optional[int] = Field(1, description="사용자 ID")
    hope_price: int = Field(..., description="희망 주택 가격 (원 단위)")
    initial_prop: int = Field(..., description="보유 자산 (원 단위)")
    loan_amount: int = Field(..., description="대출 금액 (원 단위)")


class UpdateShortageAmountResponse(BaseModel):
    tool_name: Literal["update_shortage_amount"] = Field(
        "update_shortage_amount",
        description="남은 금액 계산",
    )
    success: bool = Field(..., description="처리 성공 여부")
    user_id: int = Field(..., description="사용자 ID")
    shortage_amount: int = Field(..., description="계산된 부족 자금(원)")
    error: Optional[str] = Field(None, description="오류 메시지(실패 시)")


# ============================================================
# 6) /db/save_summary_report ---------------------------------
# ============================================================
class SaveSummaryReportRequest(BaseModel):
    user_id: Optional[int] = Field(1, description="사용자 ID")
    summary_report: str = Field(..., description="저장할 리포트 본문 (마크다운 텍스트)")


class SaveSummaryReportResponse(BaseModel):
    tool_name: Literal["save_summary_report"] = Field(
        "save_summary_report",
        description="전체 계획 생성",
    )
    success: bool = Field(..., description="처리 성공 여부")
    user_id: int = Field(..., description="사용자 ID")
    error: Optional[str] = Field(None, description="오류 메시지")


# ============================================================
# 7) /db/get_user_profile_for_fund ---------------------------
# ============================================================
class GetUserProfileForFundRequest(BaseModel):
    user_id: Optional[int] = Field(1, description="사용자 ID")


class GetUserProfileForFundResponse(BaseModel):
    tool_name: Literal["get_user_profile_for_fund"] = Field(
        "get_user_profile_for_fund",
        description="펀드 프로필",
    )
    success: bool = Field(..., description="조회 성공 여부")
    user_profile: Optional[Dict[str, Any]] = Field(
        None,
        description="펀드 추천용 사용자 프로필",
    )
    error: Optional[str] = Field(None, description="오류 메시지")


# ============================================================
# 8) /db/add_my_product --------------------------------------
# ============================================================
class AddMyProductRequest(BaseModel):
    user_id: int = Field(..., description="사용자 ID (members.user_id)")
    product_name: str = Field(..., description="상품명 (예: 'WON플러스 예금')")
    product_type: Literal["예금", "적금", "펀드"] = Field(
        ...,
        description="상품 유형: '예금' | '적금' | '펀드'",
    )
    product_description: Optional[str] = Field(
        "",
        description="상품 간단 설명",
    )
    current_value: Optional[int] = Field(
        0,
        description="현재 투자 금액 또는 가입 금액 (원 단위)",
    )
    preferential_interest_rate: Optional[float] = Field(
        None,
        description="우대 포함 예상 금리(%)",
    )
    end_date: Optional[str] = Field(
        None,
        description="만기일 (yyyy-MM-dd 형태 문자열 또는 null)",
    )


class AddMyProductResponse(BaseModel):
    tool_name: Literal["add_my_product"] = Field(
        "add_my_product",
        description="내가 투자한 상품",
    )
    success: bool = Field(..., description="처리 성공 여부")
    product_id: Optional[int] = Field(
        None,
        description="생성된 my_products.product_id",
    )
    error: Optional[str] = Field(None, description="오류 메시지(실패 시)")
    
# ============================================================
# 10) /input/get_savings_candidates ---------------------------
# ============================================================
class GetSavingsCandidatesRequest(BaseModel):
    user_data: Dict[str, Any] = Field(
        ...,
        description="members 테이블 기반 사용자 프로필(dict). "
                    "가능하면 age, salary, invest_tendency 등을 포함하세요.",
    )
    k_deposit: int = Field(
        20,
        description="예금 후보 개수",
    )
    k_saving: int = Field(
        20,
        description="적금 후보 개수",
    )


class GetSavingsCandidatesResponse(BaseModel):
    tool_name: Literal["get_savings_candidates"] = Field(
        "get_savings_candidates",
        description="예/적금 후보 조회 Tool 이름",
    )
    success: bool = Field(..., description="처리 성공 여부")
    deposit_candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="예금 후보 상품 목록",
    )
    saving_candidates: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="적금 후보 상품 목록",
    )
    error: Optional[str] = Field(
        None,
        description="오류 메시지(실패 시)",
    )


# ============================================================
# 11) /input/recommend_savings_products ----------------------
# ============================================================
class RecommendSavingsProductsRequest(BaseModel):
    user_data: Dict[str, Any] = Field(
        ...,
        description="추천에 사용할 사용자 프로필(dict)",
    )
    top_k: int = Field(
        3,
        description="예금/적금 각각 추천 개수 (기본 3)",
    )


class RecommendSavingsProductsResponse(BaseModel):
    tool_name: Literal["recommend_savings_products"] = Field(
        "recommend_savings_products",
        description="예/적금 3+3 추천 Tool",
    )
    success: bool = Field(..., description="처리 성공 여부")
    top_deposits: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="예금 추천 Top K 리스트",
    )
    top_savings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="적금 추천 Top K 리스트",
    )
    error: Optional[str] = Field(
        None,
        description="오류 메시지(실패 시)",
    )

    # ============================================================
# 12. [Fund] 사용자 투자 성향 조회
# ============================================================
class GetUserProfileForFundRequest(BaseModel):
    user_id: int = Field(..., description="사용자 ID")

class GetUserProfileForFundResponse(BaseModel):
    tool_name: str = Field("get_user_profile_for_fund", description="도구 이름")
    success: bool = Field(..., description="성공 여부")
    user_id: Optional[int] = Field(None, description="사용자 ID")
    user_name: Optional[str] = Field(None, description="사용자 이름")
    age: Optional[int] = Field(None, description="나이")
    invest_tendency: Optional[str] = Field(None, description="투자 성향")
    error: Optional[str] = Field(None, description="에러 메시지")

# ============================================================
# 13. [Fund] ML 랭킹 기반 펀드 추천 조회
# ============================================================
class GetMlRankedFundsRequest(BaseModel):
    invest_tendency: str = Field(..., description="투자 성향 (예: 공격투자형)")
    sort_by: Optional[str] = Field("score", description="정렬 기준 (score, yield_1y, yield_3m, volatility, fee, size)")

class GetMlRankedFundsResponse(BaseModel):
    tool_name: str = Field("get_ml_ranked_funds", description="도구 이름")
    success: bool = Field(..., description="성공 여부")
    funds: List[Dict[str, Any]] = Field([], description="추천 펀드 목록")
    error: Optional[str] = Field(None, description="에러 메시지")

# ============================================================
# 14. [Fund] 펀드 가입 처리 (my_products 적재)
# ============================================================
class AddMyProductRequest(BaseModel):
    user_id: int = Field(..., description="사용자 ID")
    product_name: str = Field(..., description="상품명")
    product_type: Optional[str] = Field("펀드", description="상품 유형")
    product_description: Optional[str] = Field("", description="상품 설명")

class AddMyProductResponse(BaseModel):
    tool_name: str = Field("add_my_product", description="도구 이름")
    success: bool = Field(..., description="성공 여부")
    message: Optional[str] = Field(None, description="성공 메시지")
    error: Optional[str] = Field(None, description="에러 메시지")