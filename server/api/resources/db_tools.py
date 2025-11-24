import os
import logging
import pandas as pd
from typing import Dict, Any

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
        return {
            "tool_name": "save_summary_report",
            "success": True,
            "user_id": user_id,
        }

    except Exception as e:
        logger.error(f"save_summary_report Error: {e}", exc_info=True)
        return {
            "tool_name": "save_summary_report",
            "success": False,
            "user_id": payload.get("user_id", 1),
            "error": str(e),
        }


# ============================================================
# 7. # 사용자 투자 성향 조회
# ============================================================
@router.post(
    "/get_user_profile_for_fund",
    summary="사용자 투자 성향 조회",
    operation_id="get_user_profile_for_fund",
    description=(
        "members 테이블에서 user_id를 기준으로 사용자의 투자 성향(invest_tendency)을 조회합니다.\n\n"
        "입력 필드 예시:\n"
        "- user_id: 사용자 ID (숫자 또는 문자열)\n\n"
        "출력 필드:\n"
        "- success: 조회 성공 여부(Boolean)\n"
        "- user_id: 조회된 사용자 ID\n"
        "- invest_tendency: 투자 성향 (값이 없으면 에러 반환)\n"
        "- error: 오류 메시지(실패 시)"
    ),
    response_model=dict,
)
async def api_get_user_profile_for_fund(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    members 테이블에서 사용자의 투자 성향을 조회하는 Tool.
    """
    user_id = payload.get("user_id")

    # 1. 입력값 검증
    if not user_id:
        return {
            "tool_name": "get_user_profile_for_fund",
            "success": True,
            "error": "입력값에 'user_id'가 누락되었습니다.",
        }

    try:
        with engine.connect() as conn:
            # 2. DB 조회
            query = text("""
                SELECT user_name, age, invest_tendency
                FROM members
                WHERE user_id = :uid
                LIMIT 1
            """)
            result = conn.execute(query, {"uid": user_id}).fetchone()
            
            # 3. 결과 검증
            if not result:
                # (Case A) 해당 user_id가 DB에 없는 경우
                return {
                    "tool_name": "get_user_profile_for_fund",
                    "success": False,
                    "error": f"ID가 '{user_id}'인 사용자를 찾을 수 없습니다."
                }
            
            user_name, age, invest_tendency = result
            
            if not invest_tendency:
                # (Case B) 사용자는 있는데 투자 성향이 NULL/빈 값인 경우
                # -> 펀드 추천 불가능하므로 에러 반환
                return {
                    "tool_name": "get_user_profile_for_fund",
                    "success": False,
                    "error": f"사용자('{user_name}')의 투자 성향 정보가 없습니다. 먼저 투자 성향 분석을 진행해주세요."
                }

            # 4. 성공 시 정보 반환
            return {
                "tool_name": "get_user_profile_for_fund",
                "success": True,
                "user_id": user_id,
                "user_name": user_name,
                "age": age,
                "invest_tendency": invest_tendency
            }

    except Exception as e:
        logger.error(f"get_user_profile_for_fund Error: {e}", exc_info=True)
        return {
            "tool_name": "get_user_profile_for_fund",
            "success": False,
            "error": f"DB 조회 중 오류 발생: {str(e)}",
        }


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
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    DB의 fund_ranking_snapshot 테이블에서 성향에 맞는 펀드를 조회하는 Tool.
    """
    # 1. 입력값 추출
    invest_tendency = payload.get("invest_tendency")
    sort_by = payload.get("sort_by", "score") # 기본값: 종합 점수(score)

    # 2. [Validation] 필수 값 확인
    if not invest_tendency:
        return {
            "tool_name": "get_ml_ranked_funds",
            "success": False,
            "funds": [],
            "error": "입력값에 'invest_tendency'(투자성향)가 누락되었습니다."
        }

    # [설정] 투자 성향별 허용 등급 매핑
    investor_style_to_grades = {
        '공격투자형': ["매우 높은 위험", "높은 위험", "다소 높은 위험", "보통 위험", "낮은 위험", "매우 낮은 위험"],
        '적극투자형': ["매우 높은 위험", "높은 위험", "다소 높은 위험", "보통 위험", "낮은 위험"],
        '위험중립형': ["높은 위험", "다소 높은 위험", "보통 위험", "낮은 위험"],
        '안정추구형': ["다소 높은 위험", "보통 위험", "낮은 위험", "매우 낮은 위험"],
        '안정형': ["보통 위험", "낮은 위험", "매우 낮은 위험"]
    }

    # 3. [Validation] 유효한 투자 성향인지 확인 (Fail-Fast)
    if invest_tendency not in investor_style_to_grades:
        # 정의되지 않은 성향이 들어오면 즉시 에러 반환
        return {
            "tool_name": "get_ml_ranked_funds",
            "success": False,
            "funds": [],
            "error": f"잘못된 투자 성향입니다: '{invest_tendency}'. (허용된 값: {list(investor_style_to_grades.keys())})"
        }
    
    # 유효하다면 허용 등급 가져오기
    allowed_risks = investor_style_to_grades[invest_tendency]

    # 4. 정렬 기준 매핑 (DB 한글 컬럼명 <-> 정렬 키워드)
    sort_column_map = {
        "score": "최종_종합품질점수",
        "yield_1y": "1년_수익률",
        "yield_3m": "3개월_수익률",
        "volatility": "1년_변동성",
        "fee": "총보수(%)",
        "size": "운용_규모(억)"
    }
    
    # 정렬 컬럼 결정 (매핑 안 되면 기본값 '최종_종합품질점수')
    db_sort_col = sort_column_map.get(sort_by, "최종_종합품질점수")
    
    # 오름차순 정렬이 필요한 항목 (낮을수록 좋은 것: 변동성, 수수료)
    ascending_sort_keys = ['volatility', 'fee']
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
                 "error": "펀드 데이터베이스가 비어 있습니다."
             }

        # 띄어쓰기 무시를 위한 정규화 (DB 데이터 전처리)
        df['risk_normalized'] = df['위험등급'].astype(str).str.replace(" ", "").str.strip()
        
        final_list = []
        
        # 6. 등급별 Top 2 선별
        for risk in allowed_risks:
            # 검색 키워드도 공백 제거
            search_key = risk.replace(" ", "").strip()
            
            # (1) 해당 등급 필터링 
            # (2) 점수 없는 행 제외(dropna) 
            # (3) 정렬 기준(db_sort_col)으로 정렬 
            # (4) 상위 2개 추출
            group_df = df[df['risk_normalized'] == search_key].dropna(subset=['최종_종합품질점수']).sort_values(
                by=db_sort_col, ascending=is_ascending
            ).head(2)
            
            for _, row in group_df.iterrows():
                fund_data = {
                    # --- 기본 정보 ---
                    "product_name": row['펀드명'],
                    "risk_level": row['위험등급'],
                    "description": str(row.get('설명', ''))[:500] + "..." if row.get('설명') else "설명 없음",
                    
                    # --- 점수 정보 (0~100점 스케일) ---
                    "final_quality_score": round(row['최종_종합품질점수'], 1),
                    "perf_score": round(row['종합_성과_점수'], 1),    
                    "stab_score": round(row['종합_안정성_점수'], 1),
                    
                    # --- 근거 데이터 (Evidence) - DB 한글 컬럼 매핑 ---
                    "evidence": {
                        "return_1y": row.get('1년_수익률', 0),
                        "return_3m": row.get('3개월_수익률', 0),
                        "total_fee": row.get('총보수(%)', 0),
                        "fund_size": row.get('운용_규모(억)', 0),
                        "volatility_1y": row.get('1년_변동성', 0),
                        "mdd_1y": row.get('최대_손실_낙폭(MDD)', 0)
                    }
                }
                final_list.append(fund_data)
        
        if not final_list:
            return {
                "tool_name": "get_ml_ranked_funds",
                "success": True, # 로직은 성공했으나 결과가 없는 경우
                "funds": [],
                "error": "조건에 맞는 펀드를 찾을 수 없습니다." # (DB에 데이터가 부족할 때)
            }

        logger.info(f"Invest tendency '{invest_tendency}' (Sort: {sort_by}) -> Found {len(final_list)} funds.")
        
        return {
            "tool_name": "get_ml_ranked_funds",
            "success": True,
            "funds": final_list
        }

    except Exception as e:
        logger.error(f"get_ml_ranked_funds Error: {e}", exc_info=True)
        return {
            "tool_name": "get_ml_ranked_funds",
            "success": False,
            "funds": [],
            "error": f"DB 조회 중 오류 발생: {str(e)}"
        }
    

# ============================================================
# 9. 펀드 가입 처리 (my_products 테이블 적재)
# ============================================================
@router.post(
    "/add_my_product",
    summary="사용자 펀드 가입 처리",
    operation_id="add_my_product",
    description=(
        "사용자가 선택한 펀드 상품을 'my_products' 테이블에 저장하여 가입 처리합니다.\n\n"
        "입력 필드 예시:\n"
        "- user_id: 사용자 ID (필수)\n"
        "- product_name: 펀드 상품명 (필수)\n"
        "- product_type: 상품 유형 (기본값: '펀드')\n"
        "- product_description: 펀드 설명 (선택 사항)\n"
    ),
    response_model=dict,
)
async def api_add_my_product(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    사용자가 선택한 펀드를 my_products 테이블에 INSERT하는 Tool.
    (필수 컬럼만 입력받아 처리합니다.)
    """
    user_id = payload.get("user_id")
    product_name = payload.get("product_name")
    product_type = payload.get("product_type", "펀드")
    product_description = payload.get("product_description", "")
    
    # NOT NULL 컬럼에 대한 기본값 처리
    # 예: current_value, start_date 등 필수 컬럼이 있다면 여기서 기본값을 넣어주세요.
    # current_value = 0 
    # start_date = datetime.now()

    # 1. 필수값 검증
    if not user_id or not product_name:
        return {
            "tool_name": "add_my_product",
            "success": False,
            "error": "user_id와 product_name은 필수입니다."
        }

    try:
        with engine.begin() as conn: # 트랜잭션 시작
            # 2. INSERT 쿼리 실행 (지정한 컬럼만)
            # (나머지 컬럼은 DB 설정상 NULL 허용이거나 Default가 있어야 함)
            query = text("""
                INSERT INTO my_products (user_id, product_name, product_type, product_description)
                VALUES (:uid, :pname, :ptype, :pdesc)
            """)
            
            conn.execute(query, {
                "uid": user_id,
                "pname": product_name,
                "ptype": product_type,
                "pdesc": product_description
            })

        logger.info(f"User {user_id} added fund '{product_name}' to my_products.")

        return {
            "tool_name": "add_my_product",
            "success": True,
            "message": f"'{product_name}' 상품 가입이 완료되었습니다."
        }

    except Exception as e:
        logger.error(f"add_my_product Error: {e}", exc_info=True)
        return {
            "tool_name": "add_my_product",
            "success": False,
            "error": f"DB 저장 실패: {str(e)}"
        }