import os
import logging
import json
from typing import Dict, Any, List
from fastapi import APIRouter, Body
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# 🎯 스키마 파일에서 필요한 Pydantic 모델을 임포트합니다. (수정 필요 없음)
from server.schemas.report_schema import (
    MemberDetailsInput, MemberDetailsOutput, ConsumeDataRawInput, 
    RecentReportSummaryInput, RecentReportSummaryOutput, 
    UserProductsInput, SaveMonthlyReportInput
)

# ----------------------------------
# 🌐 환경 설정 및 DB 연결
# ----------------------------------
load_dotenv()
logger = logging.getLogger(__name__)

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

# DB Engine 생성
try:
    engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")
    logger.info("✅ Report DB Tools Engine 생성 완료")
except Exception as e:
    logger.error(f"❌ DB Engine 생성 실패 (Report DB Tools): {e}")
    engine = None

# ----------------------------------
# 🛰️ 라우터 설정 (MCP 규칙 준수)
# ----------------------------------
router = APIRouter(
    prefix="/report_db",       # URL 경로 고정
    tags=["Report DB Tools"],  # FastAPI Docs 태그
)

def _safe_execute_query(query: str, params: Dict[str, Any], fetch_many: bool = False) -> List[Dict[str, Any]] | Dict[str, Any] | None:
    """DB 쿼리를 안전하게 실행하는 내부 유틸리티."""
    if engine is None: 
        logger.warning("DB Engine이 연결되지 않았습니다.")
        return None if not fetch_many else []
    try:
        with engine.connect() as conn:
            # 쿼리 실행
            result = conn.execute(text(query), params).mappings().all()
            if fetch_many: 
                return [dict(row) for row in result]
            else: 
                return dict(result[0]) if result else None
    except Exception as e:
        logger.error(f"DB 쿼리 실행 오류: {e}", exc_info=True)
        return None if not fetch_many else []

# ==============================================================================
# 1. 사용자 상세 금융/신용 정보 조회 Tool (개인 지수 변동 분석용)
# ==============================================================================
@router.post(
    "/get_member_credit_info",
    summary="사용자 상세 금융/신용 정보 조회",
    operation_id="get_report_member_details", # ⭐ Agent 호출 ID
    description="members 테이블에서 user_id를 기준으로 연봉, 부채, 신용점수 등 상세 정보를 조회합니다.",
    response_model=dict,
)
async def api_get_member_details(user_id: int = Body(..., embed=True)) -> dict:
    """개인 지수 변동 분석에 사용되는 멤버 정보를 조회합니다."""
    query = "SELECT annual_salary, total_debt, credit_score, has_house FROM members WHERE user_id = :uid LIMIT 1"
    data = _safe_execute_query(query, {"uid": user_id})
    if data: 
        return {
            "tool_name": "get_report_member_details",
            "success": True, 
            "user_id": user_id, 
            "data": data
        }
    else: 
        return {
            "tool_name": "get_report_member_details",
            "success": False, 
            "user_id": user_id, 
            "error": "멤버 상세 정보를 찾을 수 없습니다.", 
            "data": {}
        }


# ==============================================================================
# 2. 사용자 월별 소비 데이터 조회 Tool (소비 분석용)
# ==============================================================================
@router.post(
    "/get_user_consume_data_raw",
    summary="특정 월의 원시 소비 데이터 조회",
    operation_id="get_user_consume_data_raw", # ⭐ Agent 호출 ID
    description="user_consume 테이블에서 user_id와 날짜 목록을 기반으로 소비 데이터를 조회합니다.",
    response_model=dict,
)
async def api_fetch_user_consume_data(user_id: int, dates: List[str] = Body(..., embed=True)) -> dict:
    """소비 분석을 위해, 비교 대상인 직전 2개월의 소비 데이터를 조회합니다."""
    
    # 🚨 핵심 수정: spend_month 컬럼이 YYYY-MM-DD 형식일 경우를 대비하여 LIKE 검색으로 변경
    # '2022-12' -> 'spend_month LIKE '2022-12%'' 형태로 변환하여 쿼리
    like_clauses = [f"spend_month LIKE '{d}%'" for d in dates]
    where_condition = " OR ".join(like_clauses)
    
    # 쿼리 수정
    query = f"SELECT * FROM user_consume WHERE user_id = :uid AND ({where_condition})"
    
    data = _safe_execute_query(query, {"uid": user_id}, fetch_many=True)
    
    if data:
        return {
            "tool_name": "get_user_consume_data_raw",
            "success": True, 
            "user_id": user_id, 
            "data": data
        }
    else:
        return {
            "tool_name": "get_user_consume_data_raw",
            "success": False, 
            "user_id": user_id, 
            "error": "소비 데이터를 찾을 수 없습니다. (DB 형식 확인 요망)", 
            "data": []
        }

# ==============================================================================
# 3. 직전 월 레포트 요약 데이터 조회 Tool (개인 지수 비교 기준)
# ==============================================================================
@router.post(
    "/get_recent_report_summary",
    summary="가장 최근 레포트 요약 데이터 조회",
    operation_id="get_recent_report_summary", # ⭐ Agent 호출 ID
    description="reports 테이블에서 user_id의 가장 최근 보고서의 변동 데이터를 조회합니다. (개인 지수 변동 비교 기준)",
    response_model=dict,
)
async def api_fetch_recent_report_summary(member_id: int = Body(..., embed=True)) -> dict:
    """직전 월 보고서의 변동 데이터를 가져와 현재 개인 지수와의 변동 비교에 사용합니다."""
    # reports 테이블의 컬럼 이름에 맞게 change_raw_changes와 create_at 컬럼을 조회
    query = "SELECT change_raw_changes, create_at FROM reports WHERE user_id = :mid ORDER BY create_at DESC LIMIT 1"
    
    result = _safe_execute_query(query, {"mid": member_id})
    
    if result and result.get('change_raw_changes'):
        try:
            # change_raw_changes는 리스트 형태의 문자열이므로 JSON으로 로드
            raw_changes = json.loads(result['change_raw_changes'])
            
            # 여기서 직전 월의 연봉/신용점수 정보를 change_raw_changes에서 추출해야 하지만,
            # DB 스키마 개선 전까지는 일단 0으로 가정합니다.
            prev_data = {
                "annual_salary": 0, 
                "credit_score": 0,  
                "report_date": result.get('create_at')
            }
            
            return {
                "tool_name": "get_recent_report_summary",
                "success": True, 
                "member_id": member_id, 
                "data": prev_data
            }
        except json.JSONDecodeError:
            return {
                "tool_name": "get_recent_report_summary",
                "success": False, 
                "member_id": member_id, 
                "error": "데이터 파싱 오류", 
                "data": {}
            }
    else:
        # 최근 보고서를 찾을 수 없을 때 실패 반환
        return {
            "tool_name": "get_recent_report_summary",
            "success": False, 
            "member_id": member_id, 
            "error": "최근 보고서를 찾을 수 없습니다.", 
            "data": {}
        }

# ==============================================================================
# 4. 사용자 투자 상품 목록 조회 Tool (손익 분석용)
# ==============================================================================
@router.post(
    "/get_user_products",
    summary="사용자의 보유 투자 상품 목록 조회",
    operation_id="get_user_products", # ⭐ Agent 호출 ID
    description="my_products 테이블에서 user_id의 현재 보유 투자 상품 목록을 조회합니다.",
    response_model=dict,
)
async def api_fetch_user_products(user_id: int = Body(..., embed=True)) -> dict:
    """투자 손익 분석을 위한 보유 상품 목록을 조회합니다."""
    query = "SELECT * FROM my_products WHERE user_id = :uid"
    data = _safe_execute_query(query, {"uid": user_id}, fetch_many=True)
    
    if data:
        return {
            "tool_name": "get_user_products",
            "success": True, 
            "user_id": user_id, 
            "data": data
        }
    else:
        # 보유 상품이 없더라도 조회는 성공했으므로 True와 빈 리스트 반환
        return {
            "tool_name": "get_user_products",
            "success": True, 
            "user_id": user_id, 
            "error": "보유 상품이 없습니다.", 
            "data": []
        }

# ==============================================================================
# 5. 월간 보고서 저장 Tool (파이프라인 최종 저장)
# ==============================================================================
@router.post(
    "/save_monthly_report",
    summary="월간 통합 보고서 DB 저장",
    operation_id="save_report_document", # ⭐ Agent 호출 ID
    description="최종 생성된 월간 보고서(텍스트)와 분석 메타데이터를 reports 테이블의 개별 컬럼에 저장합니다.",
    response_model=dict,
)
async def api_save_monthly_report(
    member_id: int, 
    report_date: str, 
    report_text: str = Body(..., embed=False),
    metadata: Dict[str, Any] = Body(..., embed=False) # Agent가 모든 분석 결과를 담아 전달
) -> dict:
    """오케스트레이터가 완성한 최종 보고서를 DB의 개별 컬럼에 저장하는 최종 단계 Tool입니다."""
    if engine is None: 
        return {
            "tool_name": "save_report_document",
            "success": False, 
            "member_id": member_id, 
            "error": "DB 연결 오류"
        }
    
    try:
        # DB에 저장할 최종 파라미터 매핑
        params = {
            "user_id": member_id, 
            "create_at": report_date, 
            
            # JSON 데이터를 문자열로 변환하여 DB 컬럼에 저장
            "consume_report": metadata.get('consume_report', ''),
            "cluster_nickname": metadata.get('cluster_nickname', ''),
            "consume_analysis_summary": json.dumps(metadata.get('consume_analysis_summary', {}), ensure_ascii=False),
            "spend_chart_json": metadata.get('spend_chart_json', '{}'),

            "change_analysis_report": metadata.get('change_analysis_report', ''),
            "change_raw_changes": json.dumps(metadata.get('change_raw_changes', []), ensure_ascii=False),

            "profit_analysis_report": metadata.get('profit_analysis_report', ''),
            "net_profit": metadata.get('net_profit', 0),
            "profit_rate": metadata.get('profit_rate', 0.0),

            "policy_analysis_report": metadata.get('policy_analysis_report', ''),
            "policy_changes": json.dumps(metadata.get('policy_changes', []), ensure_ascii=False),
            
            "threelines_summary": metadata.get('threelines_summary', '')
        }

        # INSERT 쿼리: report_content 컬럼이 없는 DB 스키마에 맞춰 수정 완료
        column_names = ", ".join(params.keys())
        value_placeholders = ", ".join([f":{k}" for k in params.keys()])
        
        insert_query = text(f"""
            INSERT INTO reports ({column_names})
            VALUES ({value_placeholders})
        """)
            
        with engine.begin() as conn:
            conn.execute(insert_query, params)
            
            return {
                "tool_name": "save_report_document",
                "success": True, 
                "member_id": member_id, 
                "report_date": report_date
            }

    except Exception as e:
        logger.error(f"save_monthly_report Error: {e}", exc_info=True)
        return {
            "tool_name": "save_report_document",
            "success": False, 
            "member_id": member_id, 
            "error": str(e)
        }