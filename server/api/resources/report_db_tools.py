## mcp/server/api/resources/report_db_tools.py (수정됨)

import os
import logging
import json
from typing import Dict, Any, List
from fastapi import APIRouter, Body
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# 🎯 스키마 파일에서 필요한 Pydantic 모델을 임포트합니다.
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

# [Plan Agent와 동일한 ENV 변수 이름 사용]
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

# DB Engine 생성
try:
    # Plan Agent와 동일한 DB 연결 방식 사용
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
    # Note: Body 파라미터가 하나일 때, Pydantic 모델 대신 기본 타입 사용 가능
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
    placeholders = ', '.join([f"'{d}'" for d in dates])
    query = f"SELECT * FROM user_consume WHERE user_id = :uid AND spend_month IN ({placeholders})"
    
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
            "error": "소비 데이터를 찾을 수 없습니다.", 
            "data": []
        }

# ==============================================================================
# 3. 직전 월 레포트 요약 데이터 조회 Tool (개인 지수 비교 기준)
# ==============================================================================
@router.post(
    "/get_recent_report_summary",
    summary="가장 최근 레포트 요약 데이터 조회",
    operation_id="get_recent_report_summary", # ⭐ Agent 호출 ID
    description="reports 테이블에서 member_id의 가장 최근 보고서 메타데이터를 조회합니다. (개인 지수 변동 비교 기준)",
    response_model=dict,
)
async def api_fetch_recent_report_summary(member_id: int = Body(..., embed=True)) -> dict:
    """직전 월 보고서의 메타데이터를 가져와 현재 개인 지수와의 변동 비교에 사용합니다."""
    query = "SELECT metadata_json, report_date FROM reports WHERE member_id = :mid ORDER BY report_date DESC LIMIT 1"
    
    result = _safe_execute_query(query, {"mid": member_id})
    
    if result and result.get('metadata_json'):
        try:
            metadata = json.loads(result['metadata_json'])
            # 비교에 필요한 데이터만 반환
            prev_data = {
                "annual_salary": metadata.get('annual_salary'),
                "credit_score": metadata.get('credit_score'),
                "report_date": result.get('report_date')
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
        return {
            "tool_name": "get_user_products",
            "success": False, 
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
    description="최종 생성된 월간 보고서(텍스트)와 분석 메타데이터를 reports 테이블에 저장합니다.",
    response_model=dict,
)
async def api_save_monthly_report(
    member_id: int, 
    report_date: str, 
    report_text: str = Body(..., embed=False),
    metadata: Dict[str, Any] = Body(..., embed=False)
) -> dict:
    """오케스트레이터가 완성한 최종 보고서를 DB에 저장하는, 파이프라인의 최종 단계 Tool입니다."""
    if engine is None: 
        return {
            "tool_name": "save_report_document",
            "success": False, 
            "member_id": member_id, 
            "error": "DB 연결 오류"
        }
    
    try:
        with engine.begin() as conn:
            insert_query = text("""
                INSERT INTO reports (member_id, report_date, report_content, metadata_json)
                VALUES (:mid, :rdate, :content, :meta_json)
            """)
            
            conn.execute(
                insert_query,
                {"mid": member_id, "rdate": report_date, "content": report_text, "meta_json": json.dumps(metadata, ensure_ascii=False)}
            )
            
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