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

    예시:
    {
      "data": {
        "initial_prop": "3억",
        "hope_location": "서울 동작구",
        "hope_price": "10억",
        "hope_housing_type": "아파트",
        "income_usage_ratio": "30%",
        "ratio_str": "30:40:30"   # 예금:적금:펀드 비율
      }
    }
    """
    data: Dict[str, Any] = Field(
        ...,
        description=(
            "주택 계획 입력값 "
            "(initial_prop, hope_location, hope_price, "
            "hope_housing_type, income_usage_ratio, ratio_str 등)"
        ),
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
    data: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "정규화된 입력값 데이터 "
            "(initial_prop, hope_price, hope_location, "
            "hope_housing_type, income_usage_ratio, ratio_str 등)"
        ),
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
# 6) check_plan_completion ------------------------------------
# ============================================================

class CheckPlanCompletionRequest(BaseModel):
    """
    대화 메시지 기반으로 주택 자금 계획 입력이 완료되었는지 판단하는 요청 모델.
    """
    messages: List[Dict[str, Any]] = Field(
        ...,
        description="대화 메시지 리스트 (각 원소는 최소한 role, content 키를 포함하는 dict)",
    )


class CheckPlanCompletionResponse(BaseModel):
    tool_name: str = Field(
        "check_plan_completion",
        description="주택 자금 계획 입력 완료 여부 판단",
    )
    success: bool = Field(..., description="처리 성공 여부")
    is_complete: bool = Field(
        ...,
        description=(
            "입력 완료 여부 플래그\n"
            " - 현재 구현은 '마지막 assistant/ai 메시지가 \"정리해 보면\"으로 시작하면 True'로 간주합니다.\n"
            " - 논리적으로는 6개 핵심 정보( initial_prop, hope_location, hope_price,\n"
            "   hope_housing_type, income_usage_ratio, ratio_str )가 모두 채워졌는지를 의미합니다."
        ),
    )
    missing_fields: List[str] = Field(
        default_factory=list,
        description="아직 채워지지 않은 필드명 리스트(향후 고도화용)",
    )
    summary_text: Optional[str] = Field(
        None,
        description="입력이 모두 완료된 경우, '정리해 보면'으로 시작하는 요약 문단(선택)",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
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
    recommendations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="리스크 레벨별 Top1 펀드 목록",
    )
    meta: Optional[Dict[str, Any]] = Field(
        None,
        description="부가 정보 (total_input_funds, unique_risk_levels, source 등)",
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
    inputs: Optional[Dict[str, int]] = Field(
        None,
        description="계산에 사용된 입력값 (hope_price, loan_amount, initial_prop)",
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
    simulation: Optional[Dict[str, Any]] = Field(
        None,
        description="시뮬레이션 결과 (months_needed, total_balance 등)",
    )
    inputs: Optional[Dict[str, Any]] = Field(
        None,
        description="시뮬레이션에 사용된 입력값",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 10) /input/calculate_portfolio_amounts ----------------------
#      총 금액 + 비율 문자열 → 예금/적금/펀드 금액 계산
# ============================================================

class CalculatePortfolioAmountsRequest(BaseModel):
    total_amount: int = Field(
        ...,
        description="총 투자 가능 금액 (원 단위)",
    )
    ratio_str: str = Field(
        ...,
        description="예금:적금:펀드 비율 문자열 (예: '30:40:30')",
    )


class CalculatePortfolioAmountsResponse(BaseModel):
    tool_name: Literal["calculate_portfolio_amounts"] = Field(
        "calculate_portfolio_amounts",
        description="비율에 따른 금액 계산 Tool 이름",
    )
    success: bool = Field(..., description="처리 성공 여부")
    amounts: Optional[Dict[str, int]] = Field(
        None,
        description="계산된 금액 딕셔너리 (deposit, savings, fund 키 포함)",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
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
        description="멤버 테이블과 플랜 테이블 데이터 삽입/갱신",
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
        description="members + plans + loan_product JOIN 결과 조회",
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
        description="남은 금액 계산 및 plans.shortage_amount 업데이트",
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
        description="summary_report 컬럼에 최종 리포트 저장",
    )
    success: bool = Field(..., description="처리 성공 여부")
    user_id: int = Field(..., description="사용자 ID")
    error: Optional[str] = Field(None, description="오류 메시지")


# ============================================================
# 7) /db/add_my_product --------------------------------------
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
        description="my_products 테이블에 단일 상품 추가",
    )
    success: bool = Field(..., description="처리 성공 여부")
    product_id: Optional[int] = Field(
        None,
        description="생성된 my_products.product_id",
    )
    error: Optional[str] = Field(None, description="오류 메시지(실패 시)")


# ============================================================
# 8) /input/get_savings_candidates ---------------------------
# ============================================================

class GetSavingsCandidatesRequest(BaseModel):
    user_data: Dict[str, Any] = Field(
        ...,
        description=(
            "members 테이블 기반 사용자 프로필(dict). "
            "가능하면 age, salary, invest_tendency 등을 포함하세요."
        ),
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
# 9) /input/recommend_savings_products ----------------------
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
    name: Optional[str] = Field(None, description="사용자 이름")
    age: Optional[int] = Field(None, description="나이")
    invest_tendency: Optional[str] = Field(None, description="투자 성향")
    error: Optional[str] = Field(None, description="에러 메시지")


# ============================================================
# 13. [Fund] ML 랭킹 기반 펀드 추천 조회
# ============================================================

class GetMlRankedFundsRequest(BaseModel):
    invest_tendency: str = Field(..., description="투자 성향 (예: 공격투자형)")
    sort_by: Optional[str] = Field(
        "score",
        description="정렬 기준 (score, yield_1y, yield_3m, volatility, fee, size)",
    )


class GetMlRankedFundsResponse(BaseModel):
    tool_name: str = Field("get_ml_ranked_funds", description="도구 이름")
    success: bool = Field(..., description="성공 여부")
    funds: List[Dict[str, Any]] = Field([], description="추천 펀드 목록")
    error: Optional[str] = Field(None, description="에러 메시지")


# ============================================================
# 14. 펀드 전용 가입 요청 스키마
# ============================================================

class AddMyFundRequest(BaseModel):
    user_id: int = Field(..., description="사용자 ID")
    product_name: str = Field(..., description="펀드 상품명 (정확한 이름)")
    principal_amount: int = Field(1000000, description="가입 원금 (기본 100만원)")
    product_description: Optional[str] = Field("", description="상품 설명")


class AddMyFundResponse(BaseModel):
    tool_name: Literal["add_my_fund"] = Field("add_my_fund", description="도구 이름")
    success: bool = Field(..., description="성공 여부")
    product_id: Optional[int] = Field(None, description="생성된 상품 ID")
    message: Optional[str] = Field(None, description="결과 메시지")
    error: Optional[str] = Field(None, description="에러 메시지")


# ============================================================
# 15. [Portfolio] 투자 비율 조회
# ============================================================

class GetInvestmentRatioRequest(BaseModel):
    invest_tendency: str = Field(..., description="투자 성향 (예: 공격투자형)")


class GetInvestmentRatioResponse(BaseModel):
    tool_name: str = Field("get_investment_ratio", description="도구 이름")
    success: bool = Field(..., description="성공 여부")
    invest_tendency: Optional[str] = Field(None, description="요청한 투자 성향")
    recommended_ratios: Optional[Dict[str, int]] = Field(
        None,
        description="추천 비율 (예금, 적금, 펀드)",
    )
    core_logic: Optional[str] = Field(None, description="추천 논리")
    error: Optional[str] = Field(None, description="에러 메시지")


# ============================================================
# 16. [Portfolio] 자산 배분 결과 저장
# ============================================================

class SaveUserPortfolioRequest(BaseModel):
    user_id: int = Field(..., description="사용자 ID")
    deposit_amount: int = Field(
        ...,
        description="예금 배분 금액(원). DB에서는 members.deposite_amount 로 저장",
    )
    savings_amount: int = Field(
        ...,
        description="적금 배분 금액(원). DB에서는 members.saving_amount 로 저장",
    )
    fund_amount: int = Field(
        ...,
        description="펀드 배분 금액(원). DB에서는 members.fund_amount 로 저장",
    )


class SaveUserPortfolioResponse(BaseModel):
    tool_name: Literal["save_user_portfolio"] = Field(
        "save_user_portfolio",
        description="포트폴리오 배분 금액 저장 Tool 이름",
    )
    success: bool = Field(..., description="처리 성공 여부")
    message: Optional[str] = Field(
        None,
        description="성공/정보 메시지",
    )
    error: Optional[str] = Field(
        None,
        description="오류 메시지(실패 시)",
    )


# ============================================================
# 17. [Portfolio] 예금/적금/펀드 보유 금액 조회
# ============================================================

class GetMemberInvestmentAmountsRequest(BaseModel):
    user_id: int = Field(..., description="사용자 ID")


class GetMemberInvestmentAmountsResponse(BaseModel):
    tool_name: Literal["get_member_investment_amounts"] = Field(
        "get_member_investment_amounts",
        description="예금/적금/펀드 금액 조회 Tool 이름",
    )
    success: bool = Field(..., description="조회 성공 여부")
    user_id: Optional[int] = Field(None, description="사용자 ID")
    deposit_amount: Optional[int] = Field(
        None,
        description=(
            "예금 금액(원). DB 컬럼 members.deposite_amount 를 "
            "0 NULL → 0 으로 변환해 반환"
        ),
    )
    savings_amount: Optional[int] = Field(
        None,
        description=(
            "적금 금액(원). DB 컬럼 members.saving_amount 를 "
            "0 NULL → 0 으로 변환해 반환"
        ),
    )
    fund_amount: Optional[int] = Field(
        None,
        description=(
            "펀드 금액(원). DB 컬럼 members.fund_amount 를 "
            "0 NULL → 0 으로 변환해 반환"
        ),
    )
    error: Optional[str] = Field(
        None,
        description="에러 메시지(실패 시)",
    )


# ============================================================
# 18. [Portfolio] 선택한 예금/적금 금액 검증
#      - members.deposite_amount / saving_amount 한도 내인지 체크
# ============================================================

class SelectedProductAmount(BaseModel):
    """
    사용자가 선택한 단일 상품과 그 상품에 넣고 싶은 금액 정보.
    예금/적금 공통으로 사용.
    """
    product_name: str = Field(..., description="상품명 (예: 'WON플러스 예금')")
    amount: int = Field(..., description="이 상품에 넣고 싶은 금액(원 단위)")
    end_date: Optional[str] = Field(
        None,
        description="만기일 (yyyy-MM-dd 문자열, 없으면 None)",
    )


class ValidateSelectedSavingsProductsRequest(BaseModel):
    """
    /input/validate_selected_savings_products 요청 스키마

    - deposit_amount: 예금 배정 가능 총액
        · DB 컬럼 members.deposite_amount 값을
          /db/get_member_investment_amounts Tool에서 받아 사용
    - savings_amount: 적금 배정 가능 총액
        · DB 컬럼 members.saving_amount 값을
          /db/get_member_investment_amounts Tool에서 받아 사용
    - selected_deposits: 사용자가 선택한 예금 상품 목록
    - selected_savings: 사용자가 선택한 적금 상품 목록
    """
    deposit_amount: int = Field(
        ...,
        description=(
            "예금 배정 가능 총액 (원 단위). "
            "DB에서는 members.deposite_amount 값이 소스"
        ),
    )
    savings_amount: int = Field(
        ...,
        description=(
            "적금 배정 가능 총액 (원 단위). "
            "DB에서는 members.saving_amount 값이 소스"
        ),
    )
    selected_deposits: List[SelectedProductAmount] = Field(
        default_factory=list,
        description="사용자가 선택한 예금 상품 목록",
    )
    selected_savings: List[SelectedProductAmount] = Field(
        default_factory=list,
        description="사용자가 선택한 적금 상품 목록",
    )


class ValidateSelectedSavingsProductsResponse(BaseModel):
    tool_name: Literal["validate_selected_savings_products"] = Field(
        "validate_selected_savings_products",
        description="선택 예금/적금 금액 검증 Tool 이름",
    )
    success: bool = Field(..., description="검증 성공 여부 (한도 내면 True)")
    deposit_amount: int = Field(
        ...,
        description="예금 한도 (원 단위, members.deposite_amount 기준)",
    )
    savings_amount: int = Field(
        ...,
        description="적금 한도 (원 단위, members.saving_amount 기준)",
    )
    total_selected_deposit: int = Field(
        ...,
        description="선택한 예금 상품 금액 합계(원)",
    )
    total_selected_savings: int = Field(
        ...,
        description="선택한 적금 상품 금액 합계(원)",
    )
    remaining_deposit_amount: int = Field(
        ...,
        description="예금 한도 - 선택 예금 총액 (음수면 초과)",
    )
    remaining_savings_amount: int = Field(
        ...,
        description="적금 한도 - 선택 적금 총액 (음수면 초과)",
    )
    violations: List[str] = Field(
        default_factory=list,
        description="한도 초과/유효성 문제를 설명하는 메시지 리스트",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# ============================================================
# 19. [Portfolio/DB] 선택한 예금/적금 상품 my_products 저장
#      - /db/save_selected_savings_products
# ============================================================

class SaveSelectedSavingsProductsRequest(BaseModel):
    """
    /db/save_selected_savings_products 요청 스키마

    saving_agent에서 검증이 끝난 뒤,
    최종 선택된 예금/적금 상품을 my_products에 저장할 때 사용.
    """
    user_id: int = Field(..., description="사용자 ID (members.user_id)")
    selected_deposits: List[SelectedProductAmount] = Field(
        default_factory=list,
        description="저장할 예금 상품 목록",
    )
    selected_savings: List[SelectedProductAmount] = Field(
        default_factory=list,
        description="저장할 적금 상품 목록",
    )


class SaveSelectedSavingsProductsResponse(BaseModel):
    tool_name: Literal["save_selected_savings_products"] = Field(
        "save_selected_savings_products",
        description="선택 예금/적금 상품을 my_products에 저장하는 Tool 이름",
    )
    success: bool = Field(..., description="저장 성공 여부")
    user_id: Optional[int] = Field(None, description="사용자 ID")
    inserted_count: int = Field(
        ...,
        description="my_products에 실제로 INSERT된 상품 개수",
    )
    products: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "저장된 상품 정보 리스트 "
            "(각 원소: product_id, product_name, product_type, amount, display_id 등)"
        ),
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# -----------------------------
# 19. [Fund] 선택한 펀드 금액 검증
# -----------------------------
class SelectedFundAmount(BaseModel):
    fund_name: str = Field(..., description="펀드 상품명")
    amount: int = Field(..., description="해당 펀드에 투자하려는 금액(원 단위)")


class ValidateSelectedFundsProductsRequest(BaseModel):
    fund_amount: int = Field(
        ...,
        description="펀드 배정 가능 총액 (members.fund_amount)",
    )
    selected_funds: List[SelectedFundAmount] = Field(
        default_factory=list,
        description="사용자가 선택한 펀드 + 금액 목록",
    )


class ValidateSelectedFundsProductsResponse(BaseModel):
    tool_name: Literal["validate_selected_funds_products"] = Field(
        "validate_selected_funds_products",
        description="선택 펀드 금액 검증 Tool 이름",
    )
    success: bool = Field(..., description="검증 성공 여부 (한도 내면 True)")
    fund_amount: int = Field(
        ...,
        description="펀드 한도 (원 단위, members.fund_amount 기준)",
    )
    total_selected_fund: int = Field(
        ...,
        description="선택한 펀드 금액 합계(원)",
    )
    remaining_fund_amount: int = Field(
        ...,
        description="펀드 한도 - 선택 펀드 총액 (음수면 초과)",
    )
    violations: List[str] = Field(
        default_factory=list,
        description="한도 초과/유효성 문제를 설명하는 메시지 리스트",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )


# -----------------------------
# 20. [Fund] 선택 펀드 일괄 저장
# -----------------------------
class SaveSelectedFundItem(BaseModel):
    fund_name: str = Field(..., description="펀드 상품명")
    amount: int = Field(..., description="투자 금액(원)")
    fund_description: Optional[str] = Field(
        None,
        description="펀드 간단 설명(선택)",
    )
    expected_yield: Optional[float] = Field(
        None,
        description="예상 수익률(%) (선택)",
    )
    end_date: Optional[str] = Field(
        None,
        description="만기일 또는 목표 투자 기간 종료일 (yyyy-MM-dd, 선택)",
    )


class SaveSelectedFundsProductsRequest(BaseModel):
    user_id: int = Field(..., description="사용자 ID")
    selected_funds: List[SaveSelectedFundItem] = Field(
        default_factory=list,
        description="저장할 펀드 선택 목록",
    )


class SaveSelectedFundsProductsResponse(BaseModel):
    tool_name: Literal["save_selected_funds_products"] = Field(
        "save_selected_funds_products",
        description="선택 펀드 my_products 일괄 저장 Tool 이름",
    )
    success: bool = Field(..., description="저장 성공 여부")
    user_id: int = Field(..., description="사용자 ID")
    saved_products: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="실제로 저장된 my_products 레코드 요약 목록",
    )
    error: Optional[str] = Field(
        None,
        description="에러 발생 시 에러 메시지",
    )
