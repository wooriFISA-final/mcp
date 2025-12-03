import os
import logging
import pandas as pd
from typing import Dict, Any, List
from datetime import date

from fastapi import APIRouter, Body
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from dotenv import load_dotenv

# ✅ Pydantic 스키마 임포트
from server.schemas.plan_schema import (
    GetMarketPriceRequest,
    GetMarketPriceResponse,
    UpsertMemberAndPlanRequest,
    UpsertMemberAndPlanResponse,
    SaveUserPortfolioRequest,
    SaveUserPortfolioResponse,
    UpdateLoanResultRequest,
    UpdateLoanResultResponse,
    GetUserLoanOverviewRequest,
    GetUserLoanOverviewResponse,
    UpdateShortageAmountRequest,
    UpdateShortageAmountResponse,
    SaveSummaryReportRequest,
    SaveSummaryReportResponse,
    GetUserProfileForFundRequest,
    GetUserProfileForFundResponse,
    AddMyProductRequest,
    AddMyProductResponse,
    AddMyFundRequest,
    AddMyFundResponse,
    GetMemberInvestmentAmountsRequest,
    GetMemberInvestmentAmountsResponse,
    SaveSelectedSavingsProductsRequest,
    SaveSelectedSavingsProductsResponse,
    SaveSelectedFundsProductsRequest,
    SaveSelectedFundsProductsResponse,
    GetUserFullProfileRequest,
    GetUserFullProfileResponse,
    GetUserProductsRequest,
    GetUserProductsResponse,
    GetUserLoanInfoRequest,
    GetUserLoanInfoResponse
)

# ----------------------------------
# 🌐 환경 설정 및 로깅
# ----------------------------------
load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

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
# ----------------------------------
# 🛰️ 라우터 설정
# ----------------------------------
router = APIRouter(
    prefix="/db",
    tags=["DB Tools"],
)

# ============================================================
# 1. state 테이블에서 지역+주택유형 평균 시세 조회
# ============================================================
@router.post(
    "/check_house_price",
    summary="지역·주택유형 평균 시세 조회",
    operation_id="check_house_price",
    response_model=GetMarketPriceResponse,
)
async def api_check_house_price(
    payload: GetMarketPriceRequest = Body(...),
) -> GetMarketPriceResponse:
    """
    state 테이블에서 지역 + 주택유형별 평균 시세를 조회하고,
    사용자 희망 가격이 시세 범위(평균 ± 50%) 내에 있는지 검증하는 Tool.
    """
    location = payload.location.strip()
    housing_type = payload.housing_type.strip()
    user_price_str = payload.user_house_price.strip()

    if not location or not housing_type:
        return GetMarketPriceResponse(
            success=False,
            avg_price=0,
            error="location과 housing_type은 필수입니다.",
        )

    # 사용자 입력 가격을 숫자로 변환
    try:
        user_price = int(user_price_str.replace(",", "").replace("원", "").replace("만", "0000"))
    except ValueError:
        return GetMarketPriceResponse(
            success=False,
            avg_price=0,
            error="user_house_price를 숫자로 변환할 수 없습니다.",
        )

    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT 
                    CASE 
                        WHEN :housing_type = '아파트' THEN apartment_price
                        WHEN :housing_type = '오피스텔' THEN officetel_price
                        WHEN :housing_type = '연립다세대' THEN multi_price
                        WHEN :housing_type = '단독다가구' THEN detached_price
                        ELSE NULL
                    END AS avg_price
                FROM state
                WHERE region_nm = :loc
                LIMIT 1
            """
            )
            avg_price = conn.execute(
                query,
                {"loc": location, "housing_type": housing_type},
            ).scalar()

        if avg_price is None or avg_price == 0:
            return GetMarketPriceResponse(
                success=False,
                avg_price=0,
                error=f"'{location}'의 '{housing_type}' 시세 정보를 찾을 수 없습니다.",
            )

        avg_price = int(avg_price)
        
        # 평균 가격의 ±50% 범위 계산
        min_price = avg_price * 0.5
        max_price = avg_price * 1.5
        
        # 사용자 희망 가격이 범위 내에 있는지 검증
        is_valid = min_price <= user_price <= max_price

        return GetMarketPriceResponse(
            success=is_valid,
            avg_price=avg_price,
            error=None if is_valid else f"희망 가격({user_price:,}원)이 시세 범위({min_price:,.0f}원 ~ {max_price:,.0f}원)를 벗어났습니다.",
        )
    except Exception as e:
        logger.error(f"get_market_price Error: {e}", exc_info=True)
        return GetMarketPriceResponse(
            success=False,
            avg_price=0,
            error=str(e),
        )


# ============================================================
# 2. 검증된 입력값을 members & plans에 저장/갱신
# ============================================================
@router.post(
    "/upsert_member_and_plan",
    summary="검증된 입력값 저장(members & plans 업데이트)",
    operation_id="upsert_member_and_plan",
    response_model=UpsertMemberAndPlanResponse,
)
async def api_upsert_member_and_plan(
    payload: UpsertMemberAndPlanRequest = Body(...),
) -> UpsertMemberAndPlanResponse:
    """
    ValidationAgent에서 사용하던 upsert_member_and_plan을
    HTTP Tool 형태로 노출한 버전.
    """
    try:
        user_id: int = payload.user_id or 1

        initial_prop = payload.initial_prop
        hope_location = payload.hope_location
        hope_price = payload.hope_price
        hope_housing_type = payload.hope_housing_type
        income_usage_ratio = payload.income_usage_ratio

        with engine.begin() as conn:
            # 1) members 업데이트
            conn.execute(
                text(
                    """
                    UPDATE members
                    SET initial_prop = :initial_prop,
                        hope_location = :hope_location,
                        hope_price = :hope_price,
                        hope_housing_type = :hope_housing_type,
                        income_usage_ratio = :income_usage_ratio
                    WHERE user_id = :user_id
                """
                ),
                {
                    "user_id": user_id,
                    "initial_prop": initial_prop,
                    "hope_location": hope_location,
                    "hope_price": hope_price,
                    "hope_housing_type": hope_housing_type,
                    "income_usage_ratio": income_usage_ratio,
                },
            )

            # 2) 최신 plan 존재 여부 확인
            existing_plan_id = conn.execute(
                text(
                    "SELECT plan_id FROM plans "
                    "WHERE user_id = :uid ORDER BY plan_id DESC LIMIT 1"
                ),
                {"uid": user_id},
            ).scalar()

            if existing_plan_id:
                # 기존 플랜 갱신
                conn.execute(
                    text(
                        """
                        UPDATE plans
                        SET target_loc = :target_loc,
                            target_build_type = :target_build_type,
                            create_at = NOW(),
                            plan_status = '진행중'
                        WHERE plan_id = :pid
                    """
                    ),
                    {
                        "pid": existing_plan_id,
                        "target_loc": hope_location,
                        "target_build_type": hope_housing_type,
                    },
                )
            else:
                # 신규 플랜 생성
                conn.execute(
                    text(
                        """
                        INSERT INTO plans (user_id, target_loc, target_build_type, create_at, plan_status)
                        VALUES (:user_id, :target_loc, :target_build_type, NOW(), '진행중')
                    """
                    ),
                    {
                        "user_id": user_id,
                        "target_loc": hope_location,
                        "target_build_type": hope_housing_type,
                    },
                )

        logger.info(f"💾 DB upsert 완료 — user_id={user_id}")
        return UpsertMemberAndPlanResponse(
            success=True,
            user_id=user_id,
        )

    except Exception as e:
        logger.error(f"upsert_member_and_plan Error: {e}", exc_info=True)
        return UpsertMemberAndPlanResponse(
            success=False,
            user_id=payload.user_id or 1,
            error=str(e),
        )


# ============================================================
# 3. 대출 결과 반영 (DSR/DTI 포함 가능)
# ============================================================
@router.post(
    "/update_loan_result",
    summary="대출 결과 DB 반영 (plans + members)",
    operation_id="update_loan_result",
    response_model=UpdateLoanResultResponse,
)
async def update_loan_result(
    payload: UpdateLoanResultRequest = Body(...),
) -> UpdateLoanResultResponse:
    """
    LoanAgent.update_db와 동일한 동작을 HTTP Tool로 노출한 버전.
    - plans.loan_amount, plans.product_id
    - members.shortage_amount
    를 한 번에 업데이트한다.

    ⚠️ 주의: 현재 members 테이블에는 dsr/dti 컬럼이 없으므로,
    dsr/dti 값은 DB에 저장하지 않고 응답으로만 반환한다.
    """
    try:
        user_id = payload.user_id or 1
        loan_amount = payload.loan_amount
        shortage_amount = payload.shortage_amount
        product_id = payload.product_id
        dsr = payload.dsr
        dti = payload.dti

        if product_id is None:
            return UpdateLoanResultResponse(
                success=False,
                user_id=user_id,
                updated_plan_id=None,
                dsr=dsr,
                dti=dti,
                error="product_id는 필수입니다.",
            )

        with engine.begin() as conn:
            # 1) 최신 plan_id 조회
            plan_id = conn.execute(
                text(
                    "SELECT plan_id FROM plans "
                    "WHERE user_id = :uid ORDER BY plan_id DESC LIMIT 1"
                ),
                {"uid": user_id},
            ).scalar()

            if not plan_id:
                return UpdateLoanResultResponse(
                    success=False,
                    user_id=user_id,
                    updated_plan_id=None,
                    dsr=dsr,
                    dti=dti,
                    error=f"user_id={user_id} 에 대한 plan 레코드를 찾을 수 없습니다.",
                )

            # 2) plans 업데이트 (loan_amount, product_id)
            conn.execute(
                text(
                    """
                    UPDATE plans
                    SET loan_amount = :loan_amount,
                        product_id = :pid
                    WHERE plan_id = :pid_plan
                """
                ),
                {
                    "loan_amount": loan_amount,
                    "pid": product_id,
                    "pid_plan": plan_id,
                },
            )

            # 3) members.shortage_amount 업데이트
            conn.execute(
                text(
                    """
                    UPDATE members
                    SET shortage_amount = :s
                    WHERE user_id = :uid
                """
                ),
                {"s": shortage_amount, "uid": user_id},
            )

        logger.info(
            f"✅ update_loan_result 완료 — user_id={user_id}, "
            f"plan_id={plan_id}, loan_amount={loan_amount:,}, "
            f"shortage={shortage_amount:,}, dsr={dsr}, dti={dti}"
        )
        return UpdateLoanResultResponse(
            success=True,
            user_id=user_id,
            updated_plan_id=int(plan_id),
            dsr=dsr,
            dti=dti,
        )

    except Exception as e:
        logger.error(f"update_loan_result Error: {e}", exc_info=True)
        return UpdateLoanResultResponse(
            success=False,
            user_id=payload.user_id or 1,
            updated_plan_id=None,
            dsr=payload.dsr,
            dti=payload.dti,
            error=str(e),
        )


# ============================================================
# 4. user + plan + loan_product 통합 조회 (DSR/DTI 포함)
# ============================================================
@router.post(
    "/get_user_loan_overview",
    summary="사용자 + 플랜 + 대출상품 통합 정보 조회",
    operation_id="get_user_loan_overview",
    response_model=GetUserLoanOverviewResponse,
)
async def api_get_user_loan_overview(
    payload: GetUserLoanOverviewRequest = Body(...),
) -> GetUserLoanOverviewResponse:
    user_id = payload.user_id or 1

    try:
        with engine.connect() as conn:
            # 1) 기본 정보: members + plans + loan_product
            query = text(
                """
                SELECT 
                    m.name AS name,
                    m.income_usage_ratio,
                    m.initial_prop,
                    m.hope_price,
                    p.loan_amount,
                    p.product_id,
                    l.product_name,
                    l.summary AS product_summary
                FROM members m
                JOIN plans p ON m.user_id = p.user_id
                LEFT JOIN loan_product l ON p.product_id = l.product_id
                WHERE m.user_id = :uid
                ORDER BY p.plan_id DESC
                LIMIT 1
            """
            )
            row = conn.execute(query, {"uid": user_id}).mappings().first()

            if not row:
                return GetUserLoanOverviewResponse(
                    success=False,
                    user_loan_info=None,
                    error=f"user_id={user_id} 의 정보를 찾을 수 없습니다.",
                )

            data = dict(row)

            # 2) members_info에서 최신 연월 기준 salary/DSR/DTI 보정
            mi_row = conn.execute(
                text(
                    """
                    SELECT annual_salary, DTI, DSR
                    FROM members_info
                    WHERE user_id = :uid
                    ORDER BY `year_month` DESC
                    LIMIT 1
                    """
                ),
                {"uid": user_id},
            ).mappings().first()

            if mi_row:
                data["salary"] = mi_row.get("annual_salary")
                data["dti"] = mi_row.get("DTI")
                data["dsr"] = mi_row.get("DSR")
            else:
                data["salary"] = None
                data["dti"] = None
                data["dsr"] = None

            # product_name이 비어 있고 product_id만 있는 경우 보정
            if (not data.get("product_name")) and data.get("product_id"):
                extra = conn.execute(
                    text(
                        """
                        SELECT product_name, summary 
                        FROM loan_product 
                        WHERE product_id = :pid 
                        LIMIT 1
                    """
                    ),
                    {"pid": data["product_id"]},
                ).mappings().first()
                if extra:
                    data["product_name"] = extra["product_name"]
                    data["product_summary"] = extra["summary"]

        return GetUserLoanOverviewResponse(
            success=True,
            user_loan_info=data,
        )

    except Exception as e:
        logger.error(f"get_user_loan_overview Error: {e}", exc_info=True)
        return GetUserLoanOverviewResponse(
            success=False,
            user_loan_info=None,
            error=str(e),
        )


# ============================================================
# 5. 부족금 계산 + members.shortage_amount 업데이트
# ============================================================
@router.post(
    "/update_shortage_amount",
    summary="부족 자금 계산 및 members.shortage_amount 업데이트",
    operation_id="update_shortage_amount",
    response_model=UpdateShortageAmountResponse,
)
async def api_update_shortage_amount(
    payload: UpdateShortageAmountRequest = Body(...),
) -> UpdateShortageAmountResponse:
    try:
        user_id = payload.user_id or 1
        hope_price = payload.hope_price
        initial_prop = payload.initial_prop
        loan_amount = payload.loan_amount

        shortage = max(0, hope_price - (loan_amount + initial_prop))

        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE members SET shortage_amount = :shortage WHERE user_id = :uid"
                ),
                {"shortage": shortage, "uid": user_id},
            )

        logger.info(
            f"✅ shortage_amount({shortage:,}) 업데이트 완료 "
            f"(user_id={user_id}, hope_price={hope_price:,}, "
            f"initial_prop={initial_prop:,}, loan_amount={loan_amount:,})"
        )

        return UpdateShortageAmountResponse(
            success=True,
            user_id=user_id,
            shortage_amount=shortage,
        )

    except Exception as e:
        logger.error(f"update_shortage_amount Error: {e}", exc_info=True)
        return UpdateShortageAmountResponse(
            success=False,
            user_id=payload.user_id or 1,
            shortage_amount=0,
            error=str(e),
        )


# ============================================================
# 6. 요약 리포트(summary_report) 저장
# ============================================================
@router.post(
    "/save_summary_report",
    summary="summary_report 저장 (plans 최신 플랜 업데이트)",
    operation_id="save_summary_report",
    response_model=SaveSummaryReportResponse,
)
async def api_save_summary_report(
    payload: SaveSummaryReportRequest = Body(...),
) -> SaveSummaryReportResponse:
    try:
        user_id = payload.user_id or 1
        summary_report = payload.summary_report.strip()

        if not summary_report:
            return SaveSummaryReportResponse(
                success=False,
                user_id=user_id,
                error="summary_report 내용이 비어 있습니다.",
            )

        with engine.begin() as conn:
            # 최신 plan_id 조회
            plan_id = conn.execute(
                text(
                    "SELECT plan_id FROM plans "
                    "WHERE user_id = :uid ORDER BY plan_id DESC LIMIT 1"
                ),
                {"uid": user_id},
            ).scalar()

            if not plan_id:
                return SaveSummaryReportResponse(
                    success=False,
                    user_id=user_id,
                    error=f"user_id={user_id} 의 플랜 정보를 찾을 수 없습니다.",
                )

            # summary_report 업데이트
            conn.execute(
                text(
                    """
                    UPDATE plans
                    SET summary_report = :report
                    WHERE plan_id = :pid
                """
                ),
                {"report": summary_report, "pid": plan_id},
            )

        logger.info(f"✅ summary_report 저장 완료 (user_id={user_id}, plan_id={plan_id})")
        return SaveSummaryReportResponse(
            success=True,
            user_id=user_id,
        )

    except Exception as e:
        logger.error(f"save_summary_report Error: {e}", exc_info=True)
        return SaveSummaryReportResponse(
            success=False,
            user_id=payload.user_id or 1,
            error=str(e),
        )


# ============================================================
# 7. 사용자 투자 성향 조회 (스키마 기반으로 정리)
# ============================================================
@router.post(
    "/get_user_profile_for_fund",
    summary="사용자 투자 성향 조회",
    operation_id="get_user_profile_for_fund",
    response_model=GetUserProfileForFundResponse,
)
async def api_get_user_profile_for_fund(
    payload: GetUserProfileForFundRequest = Body(...),
) -> GetUserProfileForFundResponse:
    """
    members 테이블에서 사용자의 투자 성향을 조회하는 Tool.
    - 이름: members.name
    - 나이: members.birth_date 기준으로 계산
    """
    user_id = payload.user_id

    if not user_id:
        return GetUserProfileForFundResponse(
            success=False,
            user_id=user_id,
            name=None,
            age=None,
            invest_tendency=None,
            error="입력값에 'user_id'가 누락되었습니다.",
        )

    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT name, birth_date, invest_tendency
                FROM members
                WHERE user_id = :uid
                LIMIT 1
                """
            )
            result = conn.execute(query, {"uid": user_id}).fetchone()

            if not result:
                return GetUserProfileForFundResponse(
                    success=False,
                    user_id=user_id,
                    name=None,
                    age=None,
                    invest_tendency=None,
                    error=f"ID가 '{user_id}'인 사용자를 찾을 수 없습니다.",
                )

            name, birth_date, invest_tendency = result

            # birth_date 기반 나이 계산
            if birth_date:
                today = date.today()
                age = (
                    today.year
                    - birth_date.year
                    - ((today.month, today.day) < (birth_date.month, birth_date.day))
                )
            else:
                age = None

            if not invest_tendency:
                return GetUserProfileForFundResponse(
                    success=False,
                    user_id=user_id,
                    name=name,
                    age=age,
                    invest_tendency=None,
                    error=(
                        f"사용자('{name}')의 투자 성향 정보가 없습니다. "
                        "먼저 투자 성향 분석을 진행해주세요."
                    ),
                )

            return GetUserProfileForFundResponse(
                success=True,
                user_id=user_id,
                name=name,
                age=age,
                invest_tendency=invest_tendency,
                error=None,
            )

    except Exception as e:
        logger.error(f"get_user_profile_for_fund Error: {e}", exc_info=True)
        return GetUserProfileForFundResponse(
            success=False,
            user_id=user_id,
            name=None,
            age=None,
            invest_tendency=None,
            error=f"DB 조회 중 오류 발생: {str(e)}",
        )


# ============================================================
# 8. ml기반 종합점수 Top2 펀드 추천  + 사용자 의도에 따라 정렬
# ============================================================
@router.post(
    "/get_ml_ranked_funds",
    summary="투자성향 및 조건별 ML 펀드 랭킹 조회",
    operation_id="get_ml_ranked_funds",
    response_model=dict,
)
async def api_get_ml_ranked_funds(
    payload: Dict[str, Any] = Body(...),
) -> dict:
    """
    DB의 fund_ranking_snapshot 테이블에서 성향에 맞는 펀드를 조회하는 Tool.
    """
    # 1. 입력값 추출
    invest_tendency = payload.get("invest_tendency")
    sort_by = payload.get("sort_by", "score")  # 기본값: 종합 점수(score)

    # 2. [Validation] 필수 값 확인
    if not invest_tendency:
        return {
            "tool_name": "get_ml_ranked_funds",
            "success": False,
            "funds": [],
            "error": "입력값에 'invest_tendency'(투자성향)가 누락되었습니다.",
        }

    # [설정] 투자 성향별 허용 등급 매핑
    investor_style_to_grades = {
        "공격투자형": [
            "매우 높은 위험",
            "높은 위험",
            "다소 높은 위험",
            "보통 위험",
            "낮은 위험",
            "매우 낮은 위험",
        ],
        "적극투자형": [
            "매우 높은 위험",
            "높은 위험",
            "다소 높은 위험",
            "보통 위험",
            "낮은 위험",
        ],
        "위험중립형": ["높은 위험", "다소 높은 위험", "보통 위험", "낮은 위험"],
        "안정추구형": ["다소 높은 위험", "보통 위험", "낮은 위험", "매우 낮은 위험"],
        "안정형": ["보통 위험", "낮은 위험", "매우 낮은 위험"],
    }

    # 3. [Validation] 유효한 투자 성향인지 확인 (Fail-Fast)
    if invest_tendency not in investor_style_to_grades:
        return {
            "tool_name": "get_ml_ranked_funds",
            "success": False,
            "funds": [],
            "error": (
                f"잘못된 투자 성향입니다: '{invest_tendency}'. "
                f"(허용된 값: {list(investor_style_to_grades.keys())})"
            ),
        }

    allowed_risks = investor_style_to_grades[invest_tendency]

    # 4. 정렬 기준 매핑
    sort_column_map = {
        "score": "최종_종합품질점수",
        "yield_1y": "1년_수익률",
        "yield_3m": "3개월_수익률",
        "volatility": "1년_변동성",
        "fee": "총보수(%)",
        "size": "운용_규모(억)",
    }

    db_sort_col = sort_column_map.get(sort_by, "최종_종합품질점수")

    # 오름차순 정렬이 필요한 항목
    ascending_sort_keys = ["volatility", "fee"]
    is_ascending = True if sort_by in ascending_sort_keys else False

    try:
        # 5. DB 조회
        query = "SELECT * FROM fund_ranking_snapshot"
        df = pd.read_sql(query, engine)

        if df.empty:
            return {
                "tool_name": "get_ml_ranked_funds",
                "success": True,
                "funds": [],
                "error": "펀드 데이터베이스가 비어 있습니다.",
            }

        # 띄어쓰기 무시를 위한 정규화
        df["risk_normalized"] = (
            df["위험등급"].astype(str).str.replace(" ", "").str.strip()
        )

        final_list = []

        # 6. 등급별 Top 2 선별
        for risk in allowed_risks:
            search_key = risk.replace(" ", "").strip()

            group_df = (
                df[df["risk_normalized"] == search_key]
                .dropna(subset=["최종_종합품질점수"])
                .sort_values(by=db_sort_col, ascending=is_ascending)
                .head(2)
            )

            for _, row in group_df.iterrows():
                fund_data = {
                    "product_name": row["펀드명"],
                    "risk_level": row["위험등급"],
                    "description": (
                        str(row.get("설명", ""))[:500] + "..."
                        if row.get("설명")
                        else "설명 없음"
                    ),
                    "final_quality_score": round(row["최종_종합품질점수"], 1),
                    "perf_score": round(row["종합_성과_점수"], 1),
                    "stab_score": round(row["종합_안정성_점수"], 1),
                    "evidence": {
                        "return_1y": row.get("1년_수익률", 0),
                        "return_3m": row.get("3개월_수익률", 0),
                        "total_fee": row.get("총보수(%)", 0),
                        "fund_size": row.get("운용_규모(억)", 0),
                        "volatility_1y": row.get("1년_변동성", 0),
                        "mdd_1y": row.get("최대_손실_낙폭(MDD)", 0),
                    },
                }
                final_list.append(fund_data)

        if not final_list:
            return {
                "tool_name": "get_ml_ranked_funds",
                "success": True,
                "funds": [],
                "error": "조건에 맞는 펀드를 찾을 수 없습니다.",
            }

        logger.info(
            f"Invest tendency '{invest_tendency}' (Sort: {sort_by}) -> Found {len(final_list)} funds."
        )

        return {
            "tool_name": "get_ml_ranked_funds",
            "success": True,
            "funds": final_list,
        }

    except Exception as e:
        logger.error(f"get_ml_ranked_funds Error: {e}", exc_info=True)
        return {
            "tool_name": "get_ml_ranked_funds",
            "success": False,
            "funds": [],
            "error": f"DB 조회 중 오류 발생: {str(e)}",
        }


# ============================================================
# 9. 펀드 가입 처리 (my_products + my_fund_details 적재)
# ============================================================
@router.post(
    "/add_my_product",
    summary="사용자 펀드 가입 처리 (상세정보 자동 생성)",
    operation_id="add_my_product",
    description="사용자가 선택한 펀드 상품을 가입 처리합니다.",
    response_model=AddMyFundResponse,
)
async def api_add_my_fund(
    payload: AddMyFundRequest = Body(...),
) -> AddMyFundResponse:
    # 1. 입력값 추출
    user_id = payload.user_id
    product_name = payload.product_name
    principal_amount = payload.principal_amount
    product_description = payload.product_description

    # 🔹 DB ENUM('예금','적금','펀드') 와 일치하도록
    product_type = "펀드"

    if not user_id or not product_name:
        return AddMyFundResponse(
            success=False,
            product_id=None,
            message=None,
            error="user_id와 product_name은 필수입니다.",
        )

    try:
        with engine.begin() as conn:
            # 기준가 조회
            price_query = text(
                """
                SELECT 기준가 as base_price 
                FROM fund_ranking_snapshot 
                WHERE 펀드명 = :pname 
                ORDER BY 날짜 DESC 
                LIMIT 1
            """
            )
            price_row = conn.execute(
                price_query, {"pname": product_name}
            ).fetchone()

            if not price_row:
                raise ValueError(
                    f"'{product_name}' 펀드의 기준가 정보를 찾을 수 없습니다."
                )

            current_base_price = price_row[0]

            # my_products INSERT
            # 스키마: product_id, user_id, product_name, product_type,
            #        product_description, current_value,
            #        preferential_interest_rate, end_date,
            #        created_at, is_ended
            insert_product_query = text(
                """
                INSERT INTO my_products 
                (user_id, product_name, product_type, product_description,
                 current_value, preferential_interest_rate, end_date,
                 created_at, is_ended)
                VALUES 
                (:uid, :pname, :ptype, :pdesc,
                 :curr_val, NULL, NULL,
                 NOW(), 0)
            """
            )

            result = conn.execute(
                insert_product_query,
                {
                    "uid": user_id,
                    "pname": product_name,
                    "ptype": product_type,
                    "pdesc": product_description,
                    "curr_val": principal_amount,
                },
            )

            new_product_id = result.lastrowid

            # my_fund_details INSERT (스키마는 기존대로 유지한다고 가정)
            insert_detail_query = text(
                """
                INSERT INTO my_fund_details
                (product_id, fund_name, start_base_price)
                VALUES
                (:pid, :pname, :start_price)
            """
            )

            conn.execute(
                insert_detail_query,
                {
                    "pid": new_product_id,
                    "pname": product_name,
                    "start_price": current_base_price,
                },
            )

        logger.info(
            f"User {user_id} joined fund '{product_name}' "
            f"(Start Price: {current_base_price}, Amount: {principal_amount})"
        )

        return AddMyFundResponse(
            success=True,
            product_id=new_product_id,
            message=(
                f"'{product_name}' 가입 완료! "
                f"(투자금: {principal_amount:,}원, 시작가: {current_base_price:,}원)"
            ),
            error=None,
        )

    except ValueError as ve:
        logger.warning(f"add_my_product Warning: {ve}")
        return AddMyFundResponse(
            success=False,
            product_id=None,
            message=None,
            error=str(ve),
        )
    except Exception as e:
        logger.error(f"add_my_product Error: {e}", exc_info=True)
        return AddMyFundResponse(
            success=False,
            product_id=None,
            message=None,
            error=f"DB 저장 실패: {str(e)}",
        )


# ============================================================
# 10. 투자 성향별 추천 비율 조회
# ============================================================
@router.post(
    "/get_investment_ratio",
    summary="투자 성향별 추천 비율 조회",
    operation_id="get_investment_ratio",
    response_model=dict,
)
async def api_get_investment_ratio(
    payload: Dict[str, Any] = Body(...),
) -> dict:
    """
    investment_ratio_recommendation 테이블에서 성향별 포트폴리오 비율 조회 Tool.
    """
    invest_tendency = payload.get("invest_tendency")

    if not invest_tendency:
        return {
            "tool_name": "get_investment_ratio",
            "success": False,
            "error": "입력값에 'invest_tendency'(투자성향)가 누락되었습니다.",
        }

    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT deposit_ratio, savings_ratio, fund_ratio, core_logic
                FROM investment_ratio_recommendation
                WHERE invest_tendency = :tendency
                LIMIT 1
            """
            )
            row = conn.execute(query, {"tendency": invest_tendency}).fetchone()

            if not row:
                return {
                    "tool_name": "get_investment_ratio",
                    "success": False,
                    "error": (
                        f"DB에 '{invest_tendency}' 성향에 대한 추천 비율 데이터가 없습니다. "
                        "(오타 확인 필요)"
                    ),
                }

            return {
                "tool_name": "get_investment_ratio",
                "success": True,
                "invest_tendency": invest_tendency,
                "recommended_ratios": {
                    "deposit": row[0],
                    "savings": row[1],
                    "fund": row[2],
                },
                "core_logic": row[3],
            }

    except Exception as e:
        logger.error(f"get_investment_ratio Error: {e}", exc_info=True)
        return {
            "tool_name": "get_investment_ratio",
            "success": False,
            "error": f"DB 조회 중 오류 발생: {str(e)}",
        }


# ============================================================
# 11. [Portfolio] 자산 배분 결과 저장
# ============================================================
@router.post(
    "/save_user_portfolio",
    summary="사용자 자산 배분 금액 저장",
    operation_id="save_user_portfolio",
    response_model=SaveUserPortfolioResponse,
)
async def api_save_user_portfolio(
    payload: SaveUserPortfolioRequest = Body(...),
) -> SaveUserPortfolioResponse:
    """
    사용자가 결정한 예금/적금/펀드 배분 비율을 초기 자산 기준으로 금액 계산하여 저장합니다.
    - income_usage_ratio 형식: "예금:적금:펀드" (예: "30:40:30")
    - 스키마 기준 컬럼명: deposite_amount, saving_amount, fund_amount
    """
    user_id = payload.user_id
    initial_prop_str = payload.initial_prop.strip()
    income_usage_ratio = payload.income_usage_ratio.strip()

    # 초기 자산 파싱
    try:
        initial_asset = int(initial_prop_str.replace(",", "").replace("원", "").replace("만", "0000"))
    except ValueError:
        return SaveUserPortfolioResponse(
            success=False,
            error="initial_prop을 숫자로 변환할 수 없습니다.",
        )

    if initial_asset <= 0:
        return SaveUserPortfolioResponse(
            success=False,
            error="초기 자산은 0보다 커야 합니다.",
        )

    # 비율 파싱 (예: "30:40:30" -> [30, 40, 30])
    try:
        ratios = income_usage_ratio.split(":")
        if len(ratios) != 3:
            return SaveUserPortfolioResponse(
                success=False,
                error="income_usage_ratio는 '예금:적금:펀드' 형식이어야 합니다 (예: '30:40:30').",
            )
        
        deposit_ratio = float(ratios[0].strip())
        savings_ratio = float(ratios[1].strip())
        fund_ratio = float(ratios[2].strip())
    except (ValueError, IndexError):
        return SaveUserPortfolioResponse(
            success=False,
            error="비율 형식이 올바르지 않습니다. 숫자로 구성된 '예금:적금:펀드' 형식이어야 합니다.",
        )

    # 비율 검증
    total_ratio = deposit_ratio + savings_ratio + fund_ratio
    if abs(total_ratio - 100) > 0.01:  # 부동소수점 오차 고려
        return SaveUserPortfolioResponse(
            success=False,
            error=f"비율 합계가 100이 아닙니다. (현재: {total_ratio})",
        )

    if deposit_ratio < 0 or savings_ratio < 0 or fund_ratio < 0:
        return SaveUserPortfolioResponse(
            success=False,
            error="배분 비율은 음수일 수 없습니다.",
        )

    # 비율을 금액으로 변환 (소수점 이하 버림)
    deposit_amount = int(initial_asset * deposit_ratio / 100)
    savings_amount = int(initial_asset * savings_ratio / 100)
    fund_amount = int(initial_asset * fund_ratio / 100)

    try:
        with engine.begin() as conn:
            # 사용자 존재 여부 확인
            check_user = conn.execute(
                text("SELECT 1 FROM members WHERE user_id=:uid"),
                {"uid": user_id},
            ).scalar()
            
            if not check_user:
                return SaveUserPortfolioResponse(
                    success=False,
                    error=f"존재하지 않는 사용자 ID({user_id})입니다.",
                )

            # 자산 배분 금액 저장
            conn.execute(
                text(
                    """
                    UPDATE members 
                    SET deposite_amount=:d, saving_amount=:s, fund_amount=:f
                    WHERE user_id=:uid
                """
                ),
                {
                    "d": deposit_amount,
                    "s": savings_amount,
                    "f": fund_amount,
                    "uid": user_id,
                },
            )

        logger.info(
            f"Portfolio saved for User {user_id}: "
            f"초기자산={initial_asset:,}, "
            f"예금={deposit_amount:,}({deposit_ratio}%), "
            f"적금={savings_amount:,}({savings_ratio}%), "
            f"펀드={fund_amount:,}({fund_ratio}%)"
        )

        return SaveUserPortfolioResponse(
            success=True,
            message=f"자산 배분이 정상적으로 저장되었습니다. (예금: {deposit_amount:,}, 적금: {savings_amount:,}, 펀드: {fund_amount:,})",
        )

    except Exception as e:
        logger.error(f"save_user_portfolio Error: {e}", exc_info=True)
        return SaveUserPortfolioResponse(
            success=False,
            error=f"DB 저장 실패: {str(e)}",
        )


# ============================================================
# 12. [Portfolio] 예금/적금/펀드 보유 금액 조회
# ============================================================
@router.post(
    "/get_member_investment_amounts",
    summary="사용자 예금/적금/펀드 금액 조회",
    operation_id="get_member_investment_amounts",
    response_model=GetMemberInvestmentAmountsResponse,
)
async def api_get_member_investment_amounts(
    payload: GetMemberInvestmentAmountsRequest = Body(...),
) -> GetMemberInvestmentAmountsResponse:
    """
    members 테이블에서 특정 사용자의 예금/적금/펀드 금액을 조회하는 Tool.
    - DB 컬럼: deposite_amount, saving_amount, fund_amount
    - 응답 필드명: deposit_amount, savings_amount, fund_amount
    """
    user_id = payload.user_id

    if not user_id:
        return GetMemberInvestmentAmountsResponse(
            success=False,
            user_id=0,
            deposit_amount=0,
            savings_amount=0,
            fund_amount=0,
            error="입력값에 'user_id'가 누락되었습니다.",
        )

    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT deposite_amount, saving_amount, fund_amount
                FROM members
                WHERE user_id = :uid
                LIMIT 1
            """
            )
            row = conn.execute(query, {"uid": user_id}).fetchone()

            if not row:
                return GetMemberInvestmentAmountsResponse(
                    success=False,
                    user_id=user_id,
                    deposit_amount=0,
                    savings_amount=0,
                    fund_amount=0,
                    error=f"user_id={user_id} 를 가진 회원을 찾을 수 없습니다.",
                )

            deposit_amount = row[0] if row[0] is not None else 0
            savings_amount = row[1] if row[1] is not None else 0
            fund_amount = row[2] if row[2] is not None else 0

        return GetMemberInvestmentAmountsResponse(
            success=True,
            user_id=user_id,
            deposit_amount=deposit_amount,
            savings_amount=savings_amount,
            fund_amount=fund_amount,
            error=None,
        )

    except Exception as e:
        logger.error(f"get_member_investment_amounts Error: {e}", exc_info=True)
        return GetMemberInvestmentAmountsResponse(
            success=False,
            user_id=user_id,
            deposit_amount=0,
            savings_amount=0,
            fund_amount=0,
            error=f"DB 조회 중 오류 발생: {str(e)}",
        )


# ============================================================
# 13. [Saving] 선택한 예금/적금 상품을 my_products에 저장
# ============================================================
@router.post(
    "/save_selected_savings_products",
    summary="선택한 예금/적금 상품을 my_products에 저장",
    operation_id="save_selected_savings_products",
    response_model=SaveSelectedSavingsProductsResponse,
)
async def api_save_selected_savings_products(
    payload: SaveSelectedSavingsProductsRequest = Body(...),
) -> SaveSelectedSavingsProductsResponse:
    """
    saving_agent에서 최종으로 선택한 예금/적금 상품을
    my_products 테이블에 여러 건 INSERT 하는 Tool.

    my_products 스키마:
    - product_id BIGINT AUTO_INCREMENT PRIMARY KEY
    - user_id BIGINT
    - product_name VARCHAR(80)
    - product_type ENUM('예금','적금','펀드')
    - product_description VARCHAR(255)
    - current_value BIGINT
    - preferential_interest_rate DOUBLE
    - end_date DATETIME
    - created_at DATETIME
    - is_ended BOOLEAN
    """
    user_id = payload.user_id
    selected_deposits = payload.selected_deposits or []
    selected_savings = payload.selected_savings or []

    if not user_id:
        return SaveSelectedSavingsProductsResponse(
            success=False,
            user_id=0,
            inserted_count=0,
            products=[],
            error="user_id는 필수입니다.",
        )

    inserted_products: List[Dict[str, Any]] = []

    try:
        with engine.begin() as conn:
            # (A) 예금
            for item in selected_deposits:
                pname = item.product_name
                amount = item.amount
                end_date = item.end_date
                product_description = item.product_description  # 추가

                if not pname or amount is None:
                    logger.warning(
                        f"[save_selected_savings_products] 잘못된 예금 항목: {item}"
                    )
                    continue

                try:
                    amount = int(amount)
                except Exception:
                    logger.warning(
                        f"[save_selected_savings_products] 예금 금액 파싱 실패: {item}"
                    )
                    continue
                if amount <= 0:
                    continue

                result = conn.execute(
                    text(
                        """
                        INSERT INTO my_products
                        (user_id, product_name, product_type,
                         product_description, current_value,
                         end_date, created_at, is_ended)
                        VALUES
                        (:uid, :pname, :ptype,
                         :pdesc, :current,
                         :end_date, NOW(), 0)
                        """
                    ),
                    {
                        "uid": user_id,
                        "pname": pname,
                        "ptype": "예금",
                        "pdesc": product_description,  # 추가
                        "current": amount,
                        "end_date": end_date,
                    },
                )

                new_id = result.lastrowid
                inserted_products.append(
                    {
                        "product_id": new_id,
                        "product_name": pname,
                        "product_type": "예금",
                        "product_description": product_description,  # 추가
                        "amount": amount,
                        "display_id": f"예금_{new_id:04d}",
                    }
                )

            # (B) 적금
            for item in selected_savings:
                pname = item.product_name
                amount = item.amount
                end_date = item.end_date
                product_description = item.product_description  # 추가

                if not pname or amount is None:
                    logger.warning(
                        f"[save_selected_savings_products] 잘못된 적금 항목: {item}"
                    )
                    continue

                try:
                    amount = int(amount)
                except Exception:
                    logger.warning(
                        f"[save_selected_savings_products] 적금 금액 파싱 실패: {item}"
                    )
                    continue
                if amount <= 0:
                    continue

                result = conn.execute(
                    text(
                        """
                        INSERT INTO my_products
                        (user_id, product_name, product_type,
                         product_description, current_value,
                         end_date, created_at, is_ended)
                        VALUES
                        (:uid, :pname, :ptype,
                         :pdesc, :current,
                         :end_date, NOW(), 0)
                        """
                    ),
                    {
                        "uid": user_id,
                        "pname": pname,
                        "ptype": "적금",
                        "pdesc": product_description,  # 추가
                        "current": amount,
                        "end_date": end_date,
                    },
                )

                new_id = result.lastrowid
                inserted_products.append(
                    {
                        "product_id": new_id,
                        "product_name": pname,
                        "product_type": "적금",
                        "product_description": product_description,  # 추가
                        "amount": amount,
                        "display_id": f"적금_{new_id:04d}",
                    }
                )

        logger.info(
            f"✅ save_selected_savings_products 완료 — user_id={user_id}, "
            f"inserted_count={len(inserted_products)}"
        )

        return SaveSelectedSavingsProductsResponse(
            success=True,
            user_id=user_id,
            inserted_count=len(inserted_products),
            products=inserted_products,
            error=None,
        )

    except Exception as e:
        logger.error(f"save_selected_savings_products Error: {e}", exc_info=True)
        return SaveSelectedSavingsProductsResponse(
            success=False,
            user_id=user_id,
            inserted_count=0,
            products=[],
            error=str(e),
        )

# ============================================================
# 14. [Fund] 선택 펀드 my_products 일괄 저장
# ============================================================
@router.post(
    "/save_selected_funds_products",
    summary="선택 펀드 my_products 일괄 저장",
    operation_id="save_selected_funds_products",
    response_model=SaveSelectedFundsProductsResponse,
)
async def save_selected_funds_products(
    payload: SaveSelectedFundsProductsRequest = Body(...),
) -> SaveSelectedFundsProductsResponse:
    """
    선택한 펀드들을 my_products에 여러 건 INSERT.

    my_products 스키마에 맞춰 principal/payment 컬럼 제거,
    current_value, preferential_interest_rate, created_at 사용.
    """
    user_id = payload.user_id
    selected_funds = payload.selected_funds or []

    if not user_id:
        return SaveSelectedFundsProductsResponse(
            success=False,
            user_id=0,
            saved_products=[],
            error="user_id는 필수입니다.",
        )

    saved_list: List[Dict[str, Any]] = []

    try:
        with engine.begin() as conn:
            for item in selected_funds:
                # 지원하는 두 필드를 우선순위로 처리: fund_name 우선, 없으면 product_name 사용
                fund_name = getattr(item, 'fund_name', None) or getattr(item, 'product_name', None)
                amount = item.amount
                fund_desc = item.fund_description or ""
                expected_yield = item.expected_yield
                end_date = item.end_date

                if not fund_name or amount is None:
                    logger.warning(
                        f"[save_selected_funds_products] 잘못된 펀드 항목: {item}"
                    )
                    continue

                try:
                    amount = int(amount)
                except Exception:
                    logger.warning(
                        f"[save_selected_funds_products] 펀드 금액 파싱 실패: {item}"
                    )
                    continue
                if amount <= 0:
                    continue

                result = conn.execute(
                    text(
                        """
                        INSERT INTO my_products
                        (user_id, product_name, product_type,
                         current_value,
                         product_description, preferential_interest_rate,
                         end_date, created_at, is_ended)
                        VALUES
                        (:uid, :pname, '펀드',
                         :current,
                         :pdesc, :rate,
                         :end_date, NOW(), 0)
                        """
                    ),
                    {
                        "uid": user_id,
                        "pname": fund_name,
                        "current": amount,
                        "pdesc": fund_desc,
                        "rate": expected_yield,
                        "end_date": end_date,
                    },
                )

                new_id = result.lastrowid
                saved_list.append(
                    {
                        "product_id": new_id,
                        "product_name": fund_name,
                        "product_type": "펀드",
                        "product_description": fund_desc,  
                        "amount": amount,
                        "expected_yield": expected_yield,  
                        "end_date": end_date,
                    }
                )

        return SaveSelectedFundsProductsResponse(
            success=True,
            user_id=user_id,
            saved_products=saved_list,
            error=None,
        )

    except Exception as e:
        logger.error(f"save_selected_funds_products Error: {e}", exc_info=True)
        return SaveSelectedFundsProductsResponse(
            success=False,
            user_id=user_id,
            saved_products=[],
            error=str(e),
        )
# ============================================================
# Summary Agent MCP Tools
# ============================================================
# 1. 사용자 프로필 정보 조회
# 2. 사용자 내투상 정보 조회

# ============================================================
# 1. 사용자 전체 프로필 조회
# ============================================================
@router.post(
    "/get_user_full_profile",
    summary="Plan 보고서용 사용자 전체 프로필 조회",
    operation_id="get_user_full_profile",
    response_model=GetUserFullProfileResponse,
)
async def api_get_user_full_profile(
    payload: GetUserFullProfileRequest = Body(...),
) -> GetUserFullProfileResponse:
    """
    Plan 보고서 생성에 필요한 사용자 기본 정보를 한번에 조회하는 Tool.
    
    Members 테이블:
    - name, hope_location, hope_price, hope_housing_type
    - deposite_amount, saving_amount, fund_amount
    - shortage_amount, initial_prop, income_usage_ratio
    
    Members_info 테이블 (가장 오래된 year_month 기준):
    - monthly_salary, annual_salary
    """
    user_id = payload.user_id

    if not user_id:
        return GetUserFullProfileResponse(
            success=False,
            user_id=None,
            error="user_id는 필수입니다.",
        )

    try:
        with engine.connect() as conn:
            # 1) Members 테이블에서 기본 정보 조회
            members_query = text(
                """
                SELECT 
                    name,
                    hope_location,
                    hope_price,
                    hope_housing_type,
                    deposite_amount,
                    saving_amount,
                    fund_amount,
                    shortage_amount,
                    initial_prop,
                    income_usage_ratio
                FROM members
                WHERE user_id = :uid
                LIMIT 1
                """
            )
            members_row = conn.execute(members_query, {"uid": user_id}).fetchone()

            if not members_row:
                return GetUserFullProfileResponse(
                    success=False,
                    user_id=user_id,
                    error=f"user_id={user_id}에 해당하는 사용자를 찾을 수 없습니다.",
                )

            # 2) Members_info 테이블에서 가장 오래된 데이터 조회
            members_info_query = text(
                """
                SELECT 
                    monthly_salary,
                    annual_salary
                FROM members_info
                WHERE user_id = :uid
                ORDER BY `year_month` ASC
                LIMIT 1
                """
            )
            members_info_row = conn.execute(members_info_query, {"uid": user_id}).fetchone()

            # Members 데이터 언패킹
            (
                name,
                hope_location,
                hope_price,
                hope_housing_type,
                deposite_amount,
                saving_amount,
                fund_amount,
                shortage_amount,
                initial_prop,
                income_usage_ratio,
            ) = members_row

            # Members_info 데이터 (없을 수 있음)
            monthly_salary = None
            annual_salary = None
            if members_info_row:
                monthly_salary, annual_salary = members_info_row

        logger.info(
            f"✅ get_user_full_profile 완료 — user_id={user_id}, name={name}"
        )

        return GetUserFullProfileResponse(
            success=True,
            user_id=user_id,
            # Members 정보
            name=name,
            hope_location=hope_location,
            hope_price=hope_price,
            hope_housing_type=hope_housing_type,
            deposite_amount=deposite_amount if deposite_amount else 0,
            saving_amount=saving_amount if saving_amount else 0,
            fund_amount=fund_amount if fund_amount else 0,
            shortage_amount=shortage_amount if shortage_amount else 0,
            initial_prop=initial_prop if initial_prop else 0,
            income_usage_ratio=income_usage_ratio if income_usage_ratio else 0,
            # Members_info 정보
            monthly_salary=monthly_salary if monthly_salary else 0,
            annual_salary=annual_salary if annual_salary else 0,
            error=None,
        )

    except Exception as e:
        logger.error(f"get_user_full_profile Error: {e}", exc_info=True)
        return GetUserFullProfileResponse(
            success=False,
            user_id=user_id,
            error=f"DB 조회 중 오류 발생: {str(e)}",
        )
        
# ============================================================
# 2. 사용자 선택 예금/적금/펀드 상품 조회
# ============================================================
@router.post(
    "/get_user_products_info",
    summary="사용자가 선택한 예금/적금/펀드 상품 조회",
    operation_id="get_user_products_info",
    response_model=GetUserProductsResponse,
)
async def api_get_user_products(
    payload: GetUserProductsRequest = Body(...),
) -> GetUserProductsResponse:
    """
    my_products 테이블에서 사용자가 선택한 예금/적금/펀드 상품 정보를 조회하는 Tool.
    
    조회 컬럼:
    - product_name: 상품명
    - product_type: 상품 유형 (예금, 적금, 펀드)
    - current_value: 저축/투자 금액
    - product_description: 상품 간략 설명
    """
    user_id = payload.user_id

    if not user_id:
        return GetUserProductsResponse(
            success=False,
            user_id=None,
            error="user_id는 필수입니다.",
        )

    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT 
                    product_name,
                    product_type,
                    current_value,
                    product_description
                FROM my_products
                WHERE user_id = :uid
                  AND product_type IN ('예금', '적금', '펀드')
                  AND is_ended = 0
                ORDER BY product_type, created_at DESC
                """
            )
            rows = conn.execute(query, {"uid": user_id}).fetchall()

            deposit_products = []
            savings_products = []
            fund_products = []
            
            total_deposit_amount = 0
            total_savings_amount = 0
            total_fund_amount = 0

            for row in rows:
                product_name, product_type, current_value, product_description = row
                
                item = {
                    "product_name": product_name,
                    "product_type": product_type,
                    "current_value": current_value if current_value else 0,
                    "product_description": product_description,
                }

                if product_type == "예금":
                    deposit_products.append(item)
                    total_deposit_amount += current_value if current_value else 0
                elif product_type == "적금":
                    savings_products.append(item)
                    total_savings_amount += current_value if current_value else 0
                elif product_type == "펀드":
                    fund_products.append(item)
                    total_fund_amount += current_value if current_value else 0

        logger.info(
            f"✅ get_user_products 완료 — user_id={user_id}, "
            f"예금={len(deposit_products)}건, 적금={len(savings_products)}건, 펀드={len(fund_products)}건"
        )

        return GetUserProductsResponse(
            success=True,
            user_id=user_id,
            deposit_products=deposit_products,
            savings_products=savings_products,
            fund_products=fund_products,
            total_deposit_count=len(deposit_products),
            total_savings_count=len(savings_products),
            total_fund_count=len(fund_products),
            total_deposit_amount=total_deposit_amount,
            total_savings_amount=total_savings_amount,
            total_fund_amount=total_fund_amount,
            error=None,
        )

    except Exception as e:
        logger.error(f"get_user_products Error: {e}", exc_info=True)
        return GetUserProductsResponse(
            success=False,
            user_id=user_id,
            error=f"DB 조회 중 오류 발생: {str(e)}",
        )
# ============================================================
# 3. Plan 보고서용 대출 정보 조회
# ============================================================
@router.post(
    "/get_user_loan_info",
    summary="Plan 보고서용 대출 정보 조회",
    operation_id="get_user_loan_info",
    response_model=GetUserLoanInfoResponse,
)
async def api_get_user_loan_info(
    payload: GetUserLoanInfoRequest = Body(...),
) -> GetUserLoanInfoResponse:
    """
    Plan 보고서 생성에 필요한 대출 정보를 조회하는 Tool.
    
    Plans 테이블:
    - loan_amount: 대출 가능 금액
    
    loan_product 테이블:
    - product_name, bank_name, summary
    - rate_description, limit_description, period_description
    - rayment_method, preferential_rate_info
    """
    user_id = payload.user_id

    if not user_id:
        return GetUserLoanInfoResponse(
            success=False,
            user_id=None,
            error="user_id는 필수입니다.",
        )

    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT 
                    p.loan_amount,
                    l.product_name,
                    l.bank_name,
                    l.summary,
                    l.rate_description,
                    l.limit_description,
                    l.period_description,
                    l.rayment_method,
                    l.preferential_rate_info
                FROM plans p
                LEFT JOIN loan_product l ON p.product_id = l.loan_product_id
                WHERE p.user_id = :uid
                ORDER BY p.plan_id DESC
                LIMIT 1
                """
            )
            row = conn.execute(query, {"uid": user_id}).fetchone()

            if not row:
                return GetUserLoanInfoResponse(
                    success=False,
                    user_id=user_id,
                    error=f"user_id={user_id}에 해당하는 플랜 정보를 찾을 수 없습니다.",
                )

            (
                loan_amount,
                product_name,
                bank_name,
                summary,
                rate_description,
                limit_description,
                period_description,
                repayment_method,
                preferential_rate_info,
            ) = row

        logger.info(
            f"✅ get_user_loan_info 완료 — user_id={user_id}, "
            f"loan_amount={loan_amount}, product={product_name}"
        )

        return GetUserLoanInfoResponse(
            success=True,
            user_id=user_id,
            loan_amount=loan_amount if loan_amount else 0,
            product_name=product_name,
            bank_name=bank_name,
            summary=summary,
            rate_description=rate_description,
            limit_description=limit_description,
            period_description=period_description,
            repayment_method=repayment_method,
            preferential_rate_info=preferential_rate_info,
            error=None,
        )

    except Exception as e:
        logger.error(f"get_user_loan_info Error: {e}", exc_info=True)
        return GetUserLoanInfoResponse(
            success=False,
            user_id=user_id,
            error=f"DB 조회 중 오류 발생: {str(e)}",
        )