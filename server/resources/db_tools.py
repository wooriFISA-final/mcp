import os
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, Body
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

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

# 1. state 테이블에서 지역+주택유형 평균 시세 조회
@router.post(
    "/get_market_price",
    summary="지역·주택유형 평균 시세 조회",
    operation_id="get_market_price",
    description=(
        "state 테이블에서 해당 지역(region_nm)과 주택유형에 따른 평균 시세를 조회합니다.\n\n"
        "입력 필드 예시:\n"
        "- location: 지역명 (예: '서울특별시 마포구')\n"
        "- housing_type: 주택유형 (예: '아파트', '오피스텔', '연립다세대', '단독다가구')\n\n"
        "출력 필드:\n"
        "- success: 조회 성공 여부(Boolean)\n"
        "- avg_price: 평균 시세(원 단위, 없으면 0)\n"
        "- error: 오류 메시지(실패 시)"
    ),
    response_model=dict,
)
async def api_get_market_price(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    state 테이블에서 지역 + 주택유형별 평균 시세를 조회하는 Tool.
    """
    location = (payload.get("location") or "").strip()
    housing_type = (payload.get("housing_type") or "").strip()

    if not location or not housing_type:
        return {
            "success": False,
            "avg_price": 0,
            "error": "location과 housing_type은 필수입니다.",
        }

    try:
        with engine.connect() as conn:
            query = text("""
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
            """)
            avg_price = conn.execute(
                query,
                {"loc": location, "housing_type": housing_type}
            ).scalar()

        return {
            "success": True,
            "avg_price": int(avg_price or 0),
        }
    except Exception as e:
        logger.error(f"get_market_price Error: {e}", exc_info=True)
        return {
            "success": False,
            "avg_price": 0,
            "error": str(e),
        }


# 2. 검증된 입력값을 members & plans에 저장/갱신
@router.post(
    "/upsert_member_and_plan",
    summary="검증된 입력값 저장(members & plans 업데이트)",
    operation_id="upsert_member_and_plan",
    description=(
        "검증이 완료된 주택 계획 입력값을 members와 plans 테이블에 저장/갱신합니다.\n\n"
        "입력 필드 예시:\n"
        "- user_id: 사용자 ID (없으면 기본값 1)\n"
        "- initial_prop: 초기 자산(원 단위, int)\n"
        "- hope_location: 희망 지역명 (예: '서울특별시 마포구')\n"
        "- hope_price: 희망 주택 가격(원 단위, int)\n"
        "- hope_housing_type: 주택 유형 (예: '아파트')\n"
        "- income_usage_ratio: 소득 중 주택 자금에 사용할 비율(%)\n\n"
        "동작:\n"
        "- members.user_id 행을 업데이트 (없으면 업데이트만 시도)\n"
        "- plans에 해당 user_id의 최신 plan이 있으면 갱신, 없으면 새로 INSERT\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부(Boolean)\n"
        "- user_id: 처리된 사용자 ID\n"
        "- error: 오류 메시지(실패 시)"
    ),
    response_model=dict,
)
async def api_upsert_member_and_plan(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    ValidationAgent에서 사용하던 upsert_member_and_plan을
    HTTP Tool 형태로 노출한 버전.
    """
    try:
        user_id: int = int(payload.get("user_id") or 1)

        # 검증된 입력 데이터
        initial_prop = int(payload.get("initial_prop") or 0)
        hope_location = str(payload.get("hope_location") or "")
        hope_price = int(payload.get("hope_price") or 0)
        hope_housing_type = str(payload.get("hope_housing_type") or "아파트")
        income_usage_ratio = int(payload.get("income_usage_ratio") or 0)

        with engine.begin() as conn:
            # 1) members 업데이트
            conn.execute(
                text("""
                    UPDATE members
                    SET initial_prop = :initial_prop,
                        hope_location = :hope_location,
                        hope_price = :hope_price,
                        hope_housing_type = :hope_housing_type,
                        income_usage_ratio = :income_usage_ratio
                    WHERE user_id = :user_id
                """),
                {
                    "user_id": user_id,
                    "initial_prop": initial_prop,
                    "hope_location": hope_location,
                    "hope_price": hope_price,
                    "hope_housing_type": hope_housing_type,
                    "income_usage_ratio": income_usage_ratio,
                }
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
                    text("""
                        UPDATE plans
                        SET target_loc = :target_loc,
                            target_build_type = :target_build_type,
                            create_at = NOW(),
                            plan_status = '진행중'
                        WHERE plan_id = :pid
                    """),
                    {
                        "pid": existing_plan_id,
                        "target_loc": hope_location,
                        "target_build_type": hope_housing_type,
                    }
                )
            else:
                # 신규 플랜 생성
                conn.execute(
                    text("""
                        INSERT INTO plans (user_id, target_loc, target_build_type, create_at, plan_status)
                        VALUES (:user_id, :target_loc, :target_build_type, NOW(), '진행중')
                    """),
                    {
                        "user_id": user_id,
                        "target_loc": hope_location,
                        "target_build_type": hope_housing_type,
                    }
                )

        logger.info(f"💾 DB upsert 완료 — user_id={user_id}")
        return {"success": True, "user_id": user_id}

    except Exception as e:
        logger.error(f"upsert_member_and_plan Error: {e}", exc_info=True)
        return {
            "success": False,
            "user_id": payload.get("user_id", 1),
            "error": str(e),
        }

# 3. 대출 결과 반영
@router.post(
    "/update_loan_result",
    summary="대출 결과 DB 반영 (plans + members)",
    operation_id="update_loan_result",
    description=(
        "LLM이나 별도 계산 로직으로 산출된 대출 결과를 DB에 반영합니다.\n\n"
        "입력 필드 예시(payload):\n"
        "- user_id: 사용자 ID (예: 1)\n"
        "- loan_amount: 최종 대출 금액 (예: 280000000)\n"
        "- shortage_amount: 부족 자금 (예: 120000000)\n"
        "- product_id: 대출 상품 ID (예: 1)\n\n"
        "동작:\n"
        "1) plans 테이블에서 해당 user_id의 최신 plan(plan_id DESC)을 찾습니다.\n"
        "2) 해당 plan의 loan_amount, product_id를 업데이트합니다.\n"
        "3) members 테이블의 shortage_amount를 업데이트합니다.\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부(Boolean)\n"
        "- user_id: 처리된 사용자 ID\n"
        "- updated_plan_id: 대출 정보가 반영된 plan_id (없으면 null)\n"
        "- error: 오류 메시지(실패 시)"
    ),
    response_model=dict,
)
async def update_loan_result(payload: Dict[str, Any] = Body(...)) -> dict:
    """
    LoanAgent.update_db와 동일한 동작을 HTTP Tool로 노출한 버전.
    - plans.loan_amount, plans.product_id
    - members.shortage_amount
    를 한 번에 업데이트한다.
    """
    try:
        user_id = int(payload.get("user_id") or 1)
        loan_amount = int(payload.get("loan_amount") or 0)
        shortage_amount = int(payload.get("shortage_amount") or 0)
        product_id = payload.get("product_id")

        if product_id is None:
            return {
                "success": False,
                "user_id": user_id,
                "updated_plan_id": None,
                "error": "product_id는 필수입니다.",
            }

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
                return {
                    "success": False,
                    "user_id": user_id,
                    "updated_plan_id": None,
                    "error": f"user_id={user_id} 에 대한 plan 레코드를 찾을 수 없습니다.",
                }

            # 2) plans 업데이트 (loan_amount, product_id)
            conn.execute(
                text("""
                    UPDATE plans
                    SET loan_amount = :loan_amount,
                        product_id = :pid
                    WHERE plan_id = :pid_plan
                """),
                {
                    "loan_amount": loan_amount,
                    "pid": product_id,
                    "pid_plan": plan_id,
                },
            )

            # 3) members.shortage_amount 업데이트
            conn.execute(
                text("UPDATE members SET shortage_amount = :s WHERE user_id = :uid"),
                {"s": shortage_amount, "uid": user_id},
            )

        logger.info(
            f"✅ update_loan_result 완료 — user_id={user_id}, "
            f"plan_id={plan_id}, loan_amount={loan_amount:,}, shortage={shortage_amount:,}"
        )
        return {
            "success": True,
            "user_id": user_id,
            "updated_plan_id": int(plan_id),
        }

    except Exception as e:
        logger.error(f"update_loan_result Error: {e}", exc_info=True)
        return {
            "success": False,
            "user_id": payload.get("user_id", 1),
            "updated_plan_id": None,
            "error": str(e),
        }
        
# 4. user + plan + loan_product 통합 조회
@router.post(
    "/get_user_loan_overview",
    summary="사용자 + 플랜 + 대출상품 통합 정보 조회",
    operation_id="get_user_loan_overview",
    description=(
        "members, plans, loan_product를 조인하여\n"
        "한 번에 SummaryAgent용 종합 정보를 조회합니다.\n\n"
        "입력:\n"
        "- user_id: 사용자 ID\n\n"
        "출력(user_loan_info):\n"
        "- user_name, salary, income_usage_ratio\n"
        "- initial_prop, hope_price, loan_amount\n"
        "- product_id, product_name, product_summary"
    ),
    response_model=dict,
)
async def api_get_user_loan_overview(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    user_id = int(payload.get("user_id") or 1)

    try:
        with engine.connect() as conn:
            query = text("""
                SELECT 
                    m.user_name,
                    m.salary,
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
            """)
            row = conn.execute(query, {"uid": user_id}).mappings().first()

            if not row:
                return {
                    "success": False,
                    "error": f"user_id={user_id} 의 정보를 찾을 수 없습니다.",
                    "user_loan_info": None,
                }

            # product_name이 비어 있고 product_id만 있는 경우 보정
            data = dict(row)
            if not data.get("product_name") and data.get("product_id"):
                extra = conn.execute(
                    text("""
                        SELECT product_name, summary 
                        FROM loan_product 
                        WHERE product_id = :pid 
                        LIMIT 1
                    """),
                    {"pid": data["product_id"]},
                ).mappings().first()
                if extra:
                    data["product_name"] = extra["product_name"]
                    data["product_summary"] = extra["summary"]

        return {
            "success": True,
            "user_loan_info": data,
        }

    except Exception as e:
        logger.error(f"get_user_loan_overview Error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "user_loan_info": None,
        }


# 5. 부족금 계산 + members.shortage_amount 업데이트
@router.post(
    "/update_shortage_amount",
    summary="부족 자금 계산 및 members.shortage_amount 업데이트",
    operation_id="update_shortage_amount",
    description=(
        "희망 주택가격, 보유 자산, 대출금액을 기반으로 부족 자금을 계산하고\n"
        "members 테이블의 shortage_amount를 갱신합니다.\n\n"
        "입력 필드:\n"
        "- user_id: 사용자 ID\n"
        "- hope_price: 희망 주택 가격 (원 단위)\n"
        "- initial_prop: 보유 자산 (원 단위)\n"
        "- loan_amount: 대출 금액 (원 단위)\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부\n"
        "- user_id: 사용자 ID\n"
        "- shortage_amount: 계산된 부족 자금"
    ),
    response_model=dict,
)
async def api_update_shortage_amount(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    try:
        user_id = int(payload.get("user_id") or 1)
        hope_price = int(payload.get("hope_price") or 0)
        initial_prop = int(payload.get("initial_prop") or 0)
        loan_amount = int(payload.get("loan_amount") or 0)

        shortage = max(0, hope_price - (loan_amount + initial_prop))

        with engine.begin() as conn:
            conn.execute(
                text("UPDATE members SET shortage_amount = :shortage WHERE user_id = :uid"),
                {"shortage": shortage, "uid": user_id},
            )

        logger.info(
            f"✅ shortage_amount({shortage:,}) 업데이트 완료 "
            f"(user_id={user_id}, hope_price={hope_price:,}, "
            f"initial_prop={initial_prop:,}, loan_amount={loan_amount:,})"
        )

        return {
            "success": True,
            "user_id": user_id,
            "shortage_amount": shortage,
        }

    except Exception as e:
        logger.error(f"update_shortage_amount Error: {e}", exc_info=True)
        return {
            "success": False,
            "user_id": payload.get("user_id", 1),
            "shortage_amount": 0,
            "error": str(e),
        }


# 6. 요약 리포트(summary_report) 저장
@router.post(
    "/save_summary_report",
    summary="summary_report 저장 (plans 최신 플랜 업데이트)",
    operation_id="save_summary_report",
    description=(
        "SummaryAgent가 생성한 맞춤형 자산관리 리포트를\n"
        "해당 사용자의 **가장 최신 plans 레코드**에 summary_report로 저장합니다.\n\n"
        "입력 필드:\n"
        "- user_id: 사용자 ID\n"
        "- summary_report: 저장할 리포트 본문 (마크다운 텍스트)\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부\n"
        "- user_id: 사용자 ID\n"
        "- error: 오류 메시지(실패 시)"
    ),
    response_model=dict,
)
async def api_save_summary_report(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    try:
        user_id = int(payload.get("user_id") or 1)
        summary_report = str(payload.get("summary_report") or "").strip()

        if not summary_report:
            return {
                "success": False,
                "user_id": user_id,
                "error": "summary_report 내용이 비어 있습니다.",
            }

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
                return {
                    "success": False,
                    "user_id": user_id,
                    "error": f"user_id={user_id} 의 플랜 정보를 찾을 수 없습니다.",
                }

            # summary_report 업데이트
            conn.execute(
                text("""
                    UPDATE plans
                    SET summary_report = :report
                    WHERE plan_id = :pid
                """),
                {"report": summary_report, "pid": plan_id},
            )

        logger.info(f"✅ summary_report 저장 완료 (user_id={user_id}, plan_id={plan_id})")
        return {
            "success": True,
            "user_id": user_id,
        }

    except Exception as e:
        logger.error(f"save_summary_report Error: {e}", exc_info=True)
        return {
            "success": False,
            "user_id": payload.get("user_id", 1),
            "error": str(e),
        }