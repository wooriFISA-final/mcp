import os
import logging

from fastapi import APIRouter, Body
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# ✅ Pydantic 스키마 임포트
from server.schemas.plan_schema import (
    GetMarketPriceRequest,
    GetMarketPriceResponse,
    UpsertMemberAndPlanRequest,
    UpsertMemberAndPlanResponse,
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
)

# ----------------------------------
# 🌐 환경 설정 및 로깅
# ----------------------------------
load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

DB_USER = os.getenv("user")
DB_PASSWORD = os.getenv("password")
DB_HOST = os.getenv("host")
DB_NAME = os.getenv("database")

engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")

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
    "/get_market_price",
    summary="지역·주택유형 평균 시세 조회",
    operation_id="get_market_price",
    response_model=GetMarketPriceResponse,
)
async def api_get_market_price(
    payload: GetMarketPriceRequest = Body(...),
) -> GetMarketPriceResponse:
    """
    state 테이블에서 지역 + 주택유형별 평균 시세를 조회하는 Tool.
    """
    location = payload.location.strip()
    housing_type = payload.housing_type.strip()

    if not location or not housing_type:
        return GetMarketPriceResponse(
            success=False,
            avg_price=0,
            error="location과 housing_type은 필수입니다.",
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

        return GetMarketPriceResponse(
            success=True,
            avg_price=int(avg_price or 0),
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
    - members.shortage_amount (+ dsr, dti 선택 업데이트)
    를 한 번에 업데이트한다.
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

            # 3) members.shortage_amount + dsr + dti 업데이트
            conn.execute(
                text(
                    """
                    UPDATE members
                    SET shortage_amount = :s,
                        dsr = COALESCE(:dsr, dsr),
                        dti = COALESCE(:dti, dti)
                    WHERE user_id = :uid
                """
                ),
                {"s": shortage_amount, "dsr": dsr, "dti": dti, "uid": user_id},
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
            query = text(
                """
                SELECT 
                    m.user_name,
                    m.salary,
                    m.income_usage_ratio,
                    m.initial_prop,
                    m.hope_price,
                    m.dsr,
                    m.dti,
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

            # product_name이 비어 있고 product_id만 있는 경우 보정
            if not data.get("product_name") and data.get("product_id"):
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
# 7. 펀드 추천용 사용자 프로필 조회
# ============================================================
@router.post(
    "/get_user_profile_for_fund",
    summary="펀드 추천용 사용자 프로필 조회",
    operation_id="get_user_profile_for_fund",
    response_model=GetUserProfileForFundResponse,
)
async def api_get_user_profile_for_fund(
    payload: GetUserProfileForFundRequest = Body(...),
) -> GetUserProfileForFundResponse:
    user_id = payload.user_id or 1

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT
                        user_id,
                        user_name,
                        age,
                        salary,
                        invest_tendency,
                        income_usage_ratio,
                        initial_prop,
                        shortage_amount,
                        hope_price
                    FROM members
                    WHERE user_id = :uid
                    LIMIT 1
                """
                ),
                {"uid": user_id},
            ).mappings().first()

        if not row:
            return GetUserProfileForFundResponse(
                success=False,
                user_profile=None,
                error=f"user_id={user_id} 의 정보를 찾을 수 없습니다.",
            )

        return GetUserProfileForFundResponse(
            success=True,
            user_profile=dict(row),
        )

    except Exception as e:
        logger.error(f"get_user_profile_for_fund Error: {e}", exc_info=True)
        return GetUserProfileForFundResponse(
            success=False,
            user_profile=None,
            error=str(e),
        )


# ============================================================
# 8. 내가 투자중인 상품 DB 추가 (my_products)
# ============================================================
@router.post(
    "/add_my_product",
    summary="사용자 보유 금융상품 추가",
    operation_id="add_my_product",
    response_model=AddMyProductResponse,
)
async def api_add_my_product(
    payload: AddMyProductRequest = Body(...),
) -> AddMyProductResponse:
    try:
        user_id = payload.user_id
        product_name = payload.product_name.strip()
        product_type = payload.product_type
        product_description = (payload.product_description or "").strip()
        current_value = payload.current_value or 0
        preferential_interest_rate = payload.preferential_interest_rate
        end_date = payload.end_date  # '2025-12-31' 같은 형태 기대

        if not user_id or not product_name or product_type not in ("예금", "적금", "펀드"):
            return AddMyProductResponse(
                success=False,
                product_id=None,
                error="user_id, product_name, product_type('예금'|'적금'|'펀드')는 필수입니다.",
            )

        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO my_products (
                        user_id,
                        product_name,
                        product_type,
                        product_description,
                        current_value,
                        preferential_interest_rate,
                        end_date,
                        created_at,
                        is_ended
                    )
                    VALUES (
                        :user_id,
                        :product_name,
                        :product_type,
                        :product_description,
                        :current_value,
                        :preferential_interest_rate,
                        :end_date,
                        NOW(),
                        FALSE
                    )
                """
                ),
                {
                    "user_id": user_id,
                    "product_name": product_name,
                    "product_type": product_type,
                    "product_description": product_description,
                    "current_value": current_value,
                    "preferential_interest_rate": preferential_interest_rate,
                    "end_date": end_date,
                },
            )
            new_id = result.lastrowid

        logger.info(
            f"✅ add_my_product 완료 — user_id={user_id}, product_id={new_id}, "
            f"name={product_name}, type={product_type}"
        )
        return AddMyProductResponse(
            success=True,
            product_id=int(new_id),
        )

    except Exception as e:
        logger.error(f"add_my_product Error: {e}", exc_info=True)
        return AddMyProductResponse(
            success=False,
            product_id=None,
            error=str(e),
        )
