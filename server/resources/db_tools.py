# 📁 mcp-server/resources/db_tool.py
# import os
# import logging
# from dotenv import load_dotenv
# from sqlalchemy import create_engine, text
# from typing import Dict, Any, Optional

###################### 이 호출이 내부적으로는 RPC 통신을 통해 mcp-server/resources/db_tool.py 안의 upsert_member_and_plan() 함수를 실행 ##################
# 환경 설정 및 로깅

# load_dotenv()
# logger = logging.getLogger(__name__)
# logging.basicConfig(level=logging.INFO)


# # DB 연결 관련 함수
# def get_db_config() -> Dict[str, str]:
#     """DB 접속 정보를 반환"""
#     return {
#         "user": os.getenv("user"),
#         "password": os.getenv("password"),
#         "host": os.getenv("host"),
#         "database": os.getenv("database"),
#     }

# def get_db_connection():
#     """SQLAlchemy 엔진 생성"""
#     cfg = get_db_config()
#     engine = create_engine(
#         f"mysql+pymysql://{cfg['user']}:{cfg['password']}@{cfg['host']}/{cfg['database']}"
#     )
#     return engine


# # 회원 & 플랜 정보 저장/갱신 함수
# def upsert_member_and_plan(parsed: Dict[str, Any], user_id: Optional[int] = 1) -> str:
#     """
#     ValidationAgent에서 넘겨받은 검증된 데이터를 DB에 저장/갱신
#     - members 테이블: 기본 정보 업데이트
#     - plans 테이블: 기존 계획 갱신 or 신규 추가
#     """
#     engine = get_db_connection()
#     try:
#         with engine.connect() as conn:
#             # 🔹 members 업데이트
#             conn.execute(text("""
#                 UPDATE members
#                 SET initial_prop=:initial_prop,
#                     hope_location=:hope_location,
#                     hope_price=:hope_price,
#                     hope_housing_type=:hope_housing_type,
#                     income_usage_ratio=:income_usage_ratio
#                 WHERE user_id=:user_id
#             """), {
#                 "user_id": user_id,
#                 "initial_prop": parsed.get("initial_prop", 0),
#                 "hope_location": parsed.get("hope_location", ""),
#                 "hope_price": parsed.get("hope_price", 0),
#                 "hope_housing_type": parsed.get("hope_housing_type", "아파트"),
#                 "income_usage_ratio": parsed.get("income_usage_ratio", 0),
#             })

#             # 🔹 plans 확인 후 업데이트 or 신규 삽입
#             existing_plan = conn.execute(
#                 text("SELECT plan_id FROM plans WHERE user_id=:uid ORDER BY plan_id DESC LIMIT 1"),
#                 {"uid": user_id}
#             ).scalar()

#             if existing_plan:
#                 conn.execute(text("""
#                     UPDATE plans
#                     SET target_loc=:target_loc,
#                         target_build_type=:target_build_type,
#                         create_at=NOW(),
#                         plan_status='진행중'
#                     WHERE plan_id=:pid
#                 """), {
#                     "pid": existing_plan,
#                     "target_loc": parsed.get("hope_location", ""),
#                     "target_build_type": parsed.get("hope_housing_type", "아파트")
#                 })
#                 logger.info(f"기존 plan_id={existing_plan} 갱신 완료")
#             else:
#                 conn.execute(text("""
#                     INSERT INTO plans (user_id, target_loc, target_build_type, create_at, plan_status)
#                     VALUES (:user_id, :target_loc, :target_build_type, NOW(), '진행중')
#                 """), {
#                     "user_id": user_id,
#                     "target_loc": parsed.get("hope_location", ""),
#                     "target_build_type": parsed.get("hope_housing_type", "아파트")
#                 })
#                 logger.info(f"신규 plan 생성 완료 (user_id={user_id})")

#             conn.commit()
#             logger.info(f"DB 업데이트 완료: user_id={user_id}")
#             return f"DB 저장 성공 (user_id={user_id})"

#     except Exception as e:
#         logger.error(f"DB 업데이트 실패: {e}")
#         return f"DB 업데이트 실패: {e}"


############# REST API 형태로 MCP 서버를 대체 ################

# 📁 mcp-server/routers/db_router.py
import os
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from dotenv import load_dotenv


# 환경 설정 및 로깅
load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# FastAPI Router
router = APIRouter(prefix="/db", tags=["Database"])


# DB 연결 관련 설정
DB_USER = os.getenv("user")
DB_PASSWORD = os.getenv("password")
DB_HOST = os.getenv("host")
DB_NAME = os.getenv("database")

engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")


# 요청 데이터 스키마
class ValidationPayload(BaseModel):
    parsed: Dict[str, Any]
    user_id: Optional[int] = 1


# 회원 & 플랜 정보 저장/갱신 API
@router.post("/upsert")
def upsert_member_and_plan(payload: ValidationPayload):
    """
    검증된 데이터를 members 및 plans 테이블에 저장 또는 갱신합니다.
    """
    parsed = payload.parsed
    user_id = payload.user_id or 1

    try:
        with engine.connect() as conn:
            # members 업데이트
            conn.execute(text("""
                UPDATE members
                SET initial_prop=:initial_prop,
                    hope_location=:hope_location,
                    hope_price=:hope_price,
                    hope_housing_type=:hope_housing_type,
                    income_usage_ratio=:income_usage_ratio
                WHERE user_id=:user_id
            """), {
                "user_id": user_id,
                "initial_prop": parsed.get("initial_prop", 0),
                "hope_location": parsed.get("hope_location", ""),
                "hope_price": parsed.get("hope_price", 0),
                "hope_housing_type": parsed.get("hope_housing_type", "아파트"),
                "income_usage_ratio": parsed.get("income_usage_ratio", 0),
            })

            # plans 확인 후 업데이트 or 신규 삽입
            existing_plan = conn.execute(
                text("SELECT plan_id FROM plans WHERE user_id=:uid ORDER BY plan_id DESC LIMIT 1"),
                {"uid": user_id}
            ).scalar()

            if existing_plan:
                conn.execute(text("""
                    UPDATE plans
                    SET target_loc=:target_loc,
                        target_build_type=:target_build_type,
                        create_at=NOW(),
                        plan_status='진행중'
                    WHERE plan_id=:pid
                """), {
                    "pid": existing_plan,
                    "target_loc": parsed.get("hope_location", ""),
                    "target_build_type": parsed.get("hope_housing_type", "아파트")
                })
                msg = f"기존 plan_id={existing_plan} 갱신 완료"
            else:
                conn.execute(text("""
                    INSERT INTO plans (user_id, target_loc, target_build_type, create_at, plan_status)
                    VALUES (:user_id, :target_loc, :target_build_type, NOW(), '진행중')
                """), {
                    "user_id": user_id,
                    "target_loc": parsed.get("hope_location", ""),
                    "target_build_type": parsed.get("hope_housing_type", "아파트")
                })
                msg = "신규 plan 생성 완료"

            conn.commit()
            logger.info(f"DB 업데이트 완료: user_id={user_id}")
            return {"status": "success", "message": msg, "user_id": user_id}

    except Exception as e:
        logger.error(f"DB 업데이트 실패: {e}")
        raise HTTPException(status_code=500, detail=f"DB 업데이트 실패: {str(e)}")
