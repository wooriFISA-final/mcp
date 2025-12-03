import os
import logging
import json
import re # 정규표현식 임포트 추가
from typing import Dict, Any, List
from fastapi import APIRouter, Body
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool
from dotenv import load_dotenv
from datetime import date as date_type, datetime as datetime_type
from decimal import Decimal

# 🎯 스키마 파일에서 필요한 Pydantic 모델을 임포트합니다. (경로에 맞게 유지)
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
    logger.info("✅ Report DB Tools Engine 생성 완료")
except Exception as e:
    logger.error(f"❌ DB Engine 생성 실패 (Report DB Tools): {e}")
    engine = None

# ----------------------------------
# 🛰️ 라우터 설정 (MCP 규칙 준수)
# ----------------------------------
router = APIRouter(
    prefix="/report_db",
    tags=["Report DB Tools"],
)

def _normalize_date_input(date_str: str) -> str | None:
    """
    다양한 날짜 입력 형식을 (YYYY-MM, YYYY_MM, YYYY-MM-DD, YYYY_MM_DD) YYYY-MM 형식으로 표준화합니다.
    """
    if not date_str:
        return None
    
    # 구분자를 모두 '-'로 통일
    normalized = date_str.replace("_", "-")
    
    # YYYY-MM-DD 또는 YYYY-MM 부분만 추출
    match = re.match(r"^\d{4}-\d{2}", normalized)
    if match:
        return match.group(0) # 예: 2025-01
        
    return None # 매칭 실패 시

def _safe_execute_query(query: str, params: Dict[str, Any], fetch_many: bool = False) -> List[Dict[str, Any]] | Dict[str, Any] | None:
    """DB 쿼리를 안전하게 실행하는 내부 유틸리티."""
    if engine is None: 
        logger.warning("DB Engine이 연결되지 않았습니다.")
        return None if not fetch_many else []
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), params).mappings().all()
            
            # 🚨 [JSON 안정성]: DB에서 가져온 날짜 객체와 Decimal 객체를 문자열/Float으로 변환
            processed_results = []
            for row in result:
                processed_row = dict(row)
                for key, value in processed_row.items():
                    if isinstance(value, (date_type, datetime_type)):
                        # 날짜/시간 객체는 YYYY-MM-DD 형식의 문자열로 변환
                        processed_row[key] = value.strftime("%Y-%m-%d")
                    elif isinstance(value, Decimal):
                        # Decimal 객체는 Float으로 변환
                        processed_row[key] = float(value) 
                processed_results.append(processed_row)
            
            if fetch_many: 
                return processed_results
            else: 
                return processed_results[0] if processed_results else None
    except Exception as e:
        logger.error(f"DB 쿼리 실행 오류: {e}", exc_info=True)
        return None if not fetch_many else []

# ==============================================================================
# 1. 사용자 상세 금융/신용 정보 조회 Tool (개인 지수 변동 분석용)
# ==============================================================================
@router.post(
    "/get_member_credit_info",
    summary="사용자 상세 금융/신용 정보 조회",
    operation_id="get_report_member_details",
    description="members와 members_info 테이블을 결합하여 user_id 기준 상세 정보를 조회합니다.",
    response_model=dict,
)
async def api_get_member_details(user_id: int = Body(..., embed=True)) -> dict:
    
    member_cols = [
        "user_id", "name", "job", "gender", "birth_date",
        "initial_prop", "currency", "deposite_amount", "saving_amount", 
        "fund_amount", "invest_tendency", "hope_location", "hope_price", 
        "hope_housing_type", "income_usage_ratio", "is_loan_possible", 
        "existing_loans", "shortage_amount"
    ]
    
    member_cols_str = ", ".join([f"`{col}`" for col in member_cols])
    
    # 1. members 테이블에서 기본 정보 조회
    member_query = f"SELECT {member_cols_str} FROM members WHERE user_id = :uid LIMIT 1"
    member_data = _safe_execute_query(member_query, {"uid": user_id})

    if not member_data: 
        return {
            "tool_name": "get_report_member_details",
            "success": False, 
            "user_id": user_id, 
            "error": "멤버 기본 정보를 찾을 수 없습니다.", 
            "data": {}
        }
    
    # 2. members_info 테이블에서 최신 월의 상세 재무 정보 조회
    info_query = """
        SELECT * FROM members_info 
        WHERE user_id = :uid 
        ORDER BY `year_month` DESC LIMIT 1
    """
    info_data = _safe_execute_query(info_query, {"uid": user_id})
    
    # 3. 데이터 결합
    final_data = dict(member_data)
    
    if info_data:
        for key, value in info_data.items():
            if key not in final_data:
                final_data[key] = value

    return {
        "tool_name": "get_report_member_details",
        "success": True, 
        "user_id": user_id, 
        "data": final_data
    }


# ==============================================================================
# 2. 사용자 월별 소비 데이터 조회 Tool (소비 분석용)
# ==============================================================================
@router.post(
    "/get_user_consume_data_raw",
    summary="특정 월의 원시 소비 데이터 조회",
    operation_id="get_user_consume_data_raw",
    description="user_consume 테이블에서 user_id와 날짜 목록을 기반으로 소비 데이터를 조회합니다.",
    response_model=dict,
)
async def api_fetch_user_consume_data(user_id: int, dates: List[str] = Body(..., embed=True)) -> dict:
    
    # 🔧 수정: 유틸리티 함수를 사용하여 YYYY-MM 형식으로 정규화 후, DB 형식인 YYYY_MM으로 변환
    normalized_dates = [_normalize_date_input(d) for d in dates]
    # None이 아닌 유효한 값만 필터링하고 DB 형식인 YYYY_MM으로 변환
    converted_dates = [d.replace("-", "_") for d in normalized_dates if d]
    
    if not converted_dates:
        return {
            "tool_name": "get_user_consume_data_raw",
            "success": False, 
            "user_id": user_id, 
            "error": "유효한 날짜 형식을 찾을 수 없습니다.", 
            "data": []
        }
        
    date_placeholders = ", ".join([f":d{i}" for i in range(len(converted_dates))])
    params = {"uid": user_id}
    params.update({f"d{i}": date_str for i, date_str in enumerate(converted_dates)})
    
    query = f"SELECT * FROM user_consume WHERE user_id = :uid AND year_and_month IN ({date_placeholders})"
    
    data = _safe_execute_query(query, params, fetch_many=True)
    
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
    summary="직전 보고서 메타데이터 조회",
    operation_id="get_recent_report_summary",
    description="reports 테이블에서 user_id와 특정 날짜를 기준으로 보고서 변동 데이터를 조회합니다.",
    response_model=dict,
)
async def api_fetch_recent_report_summary(
    member_id: int = Body(..., embed=False),
    report_date_for_comparison: str = Body(..., embed=True) 
) -> dict:
    
    # 🔧 수정: 유틸리티 함수를 사용하여 YYYY-MM 형식으로 정규화
    normalized_date_ym = _normalize_date_input(report_date_for_comparison)
    
    if not normalized_date_ym:
        return {
            "tool_name": "get_recent_report_summary",
            "success": False, 
            "member_id": member_id, 
            "error": "유효한 날짜 형식을 찾을 수 없습니다.", 
            "data": {}
        }
    
    # reports 테이블의 create_at이 YYYY-MM-DD 형식이라고 가정하고 해당 월의 '01'일로 변환
    target_date = f"{normalized_date_ym}-01"

    # 쿼리 수정: report_date_for_comparison에 해당하는 보고서만 조회
    query = """
        SELECT change_raw_changes, create_at 
        FROM reports 
        WHERE user_id = :mid AND create_at = :report_date 
        LIMIT 1
    """
    
    params = {"mid": member_id, "report_date": target_date} # 정규화된 날짜 사용
    result = _safe_execute_query(query, params)
    
    if result and result.get('change_raw_changes'):
        try:
            # change_raw_changes는 리스트 형태의 문자열이므로 JSON으로 로드
            raw_changes = json.loads(result['change_raw_changes'])
            
            # 직전 상태 정보 추출 로직 (현재는 기본값)
            prev_data = {
                "annual_salary": 0,  
                "total_debt": 0,    
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
        # 최근 보고서를 찾을 수 없을 때 실패 반환 (Agent Tool에서 초기값으로 사용)
        return {
            "tool_name": "get_recent_report_summary",
            "success": False, 
            "member_id": member_id, 
            "error": "비교 기준 보고서(직전 월)를 찾을 수 없습니다.", 
            "data": {}
        }
# ==============================================================================
# 4. 사용자 투자 상품 목록 조회 Tool (손익 분석용)
# ==============================================================================
@router.post(
    "/get_user_products",
    summary="사용자의 보유 투자 상품 목록 조회",
    operation_id="get_user_products",
    description="my_products 테이블에서 user_id의 현재 보유 투자 상품 목록을 조회합니다.",
    response_model=dict,
)
async def api_fetch_user_products(user_id: int = Body(..., embed=True)) -> dict:
    
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
        # 보유 상품이 없을 경우에도 success=True와 빈 리스트 반환 (툴의 일반적인 동작)
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
    operation_id="save_report_document",
    description="최종 생성된 월간 보고서(텍스트)와 분석 메타데이터를 reports 테이블의 개별 컬럼에 저장합니다.",
    response_model=dict,
)
async def api_save_monthly_report(
    member_id: int, 
    report_date: str, # 입력된 날짜 문자열
    report_text: str = Body(..., embed=False),
    metadata: Dict[str, Any] = Body(..., embed=False) 
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
        # 🔧 수정: 입력된 report_date를 YYYY-MM-DD 형식으로 정규화하여 DB에 저장
        normalized_date_ym = _normalize_date_input(report_date)
        if not normalized_date_ym:
             return {
                "tool_name": "save_report_document",
                "success": False, 
                "member_id": member_id, 
                "error": "유효한 보고서 날짜 형식을 찾을 수 없습니다."
            }
        
        # reports.create_at이 YYYY-MM-DD 형식이므로, '-01'을 붙여 사용
        db_report_date = f"{normalized_date_ym}-01"

        # 🚨 [JSON 안정성]: Decimal, date, datetime 객체를 문자열/Float로 변환하는 시리얼라이저 정의
        def default_json_serializer(obj):
            if isinstance(obj, Decimal): # Decimal 객체를 Float으로 변환
                return float(obj)
            if isinstance(obj, (date_type, datetime_type)): # 날짜 객체를 ISO 문자열로 변환
                return obj.isoformat()
            if isinstance(obj, bytes):
                return obj.decode('utf-8')
            raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

        # DB에 저장할 최종 파라미터 매핑
        # JSON 문자열로 변환이 필요한 필드는 json.dumps와 default_json_serializer 사용
        params = {
            "user_id": member_id, 
            "create_at": db_report_date, # 정규화된 날짜 사용
            
            "consume_report": metadata.get('consume_report', ''),
            "cluster_nickname": metadata.get('cluster_nickname', ''),
            "consume_analysis_summary": json.dumps(metadata.get('consume_analysis_summary', {}), ensure_ascii=False, default=default_json_serializer),
            "spend_chart_json": metadata.get('spend_chart_json', '{}'),

            "change_analysis_report": metadata.get('change_analysis_report', ''),
            "change_raw_changes": json.dumps(metadata.get('change_raw_changes', []), ensure_ascii=False, default=default_json_serializer),

            "profit_analysis_report": metadata.get('profit_analysis_report', ''),
            "net_profit": metadata.get('net_profit', 0),
            "profit_rate": metadata.get('profit_rate', 0.0),
            "trend_chart_json": metadata.get('trend_chart_json', '[]'),
            "fund_comparison_json": metadata.get('fund_comparison_json', '[]'),

            "policy_analysis_report": metadata.get('policy_analysis_report', ''),
            "policy_changes": json.dumps(metadata.get('policy_changes', []), ensure_ascii=False, default=default_json_serializer),
            
            "threelines_summary": metadata.get('threelines_summary', ''),
            "report_text": report_text # 최종 보고서 텍스트 필드 추가
        }

        # INSERT 쿼리: reports 테이블에 맞게 수정 완료
        column_names = ", ".join([f"`{k}`" for k in params.keys()])
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
                "report_date": db_report_date # DB에 저장된 형식 반환
            }

    except Exception as e:
        logger.error(f"save_monthly_report Error: {e}", exc_info=True)
        return {
            "tool_name": "save_report_document",
            "success": False, 
            "member_id": member_id, 
            "error": str(e)
        }


# ==============================================================================
# 6. 월별 투자 시뮬레이션 데이터 조회 Tool (그래프용)
# ==============================================================================
@router.post(
    "/get_monthly_simulation_data",
    summary="월별 투자 시뮬레이션 데이터 조회",
    operation_id="get_monthly_simulation_data",
    description="monthly_simulation_report 테이블에서 사용자의 월별 투자 데이터를 조회합니다.",
    response_model=dict,
)
async def api_get_monthly_simulation_data(
    user_id: int = Body(..., embed=True),
) -> dict:
    
    query = """
        SELECT * FROM monthly_simulation_report 
        WHERE user_id = :uid 
        ORDER BY year_and_month ASC
        LIMIT 12
    """
    params = {"uid": user_id}
    
    data = _safe_execute_query(query, params, fetch_many=True)
    
    return {
        "tool_name": "get_monthly_simulation_data",
        "success": True,
        "user_id": user_id,
        "data": data if data else []
    }

# ==============================================================================
# 7. 펀드 포트폴리오 스냅샷 조회 Tool (그래프용)
# ==============================================================================
@router.post(
    "/get_fund_portfolio_data",
    summary="펀드 포트폴리오 스냅샷 조회",
    operation_id="get_fund_portfolio_data",
    description="monthly_fund_portfolio_snapshot 테이블에서 사용자의 최신 펀드 데이터를 조회합니다.",
    response_model=dict,
)
async def api_get_fund_portfolio_data(
    user_id: int = Body(..., embed=True),
) -> dict:
    
    # 1. 가장 최신 월 찾기
    latest_month_query = """
        SELECT MAX(year_and_month) as max_month 
        FROM monthly_fund_portfolio_snapshot 
        WHERE user_id = :uid
    """
    latest_month_result = _safe_execute_query(latest_month_query, {"uid": user_id}, fetch_many=False)
    
    if not latest_month_result or not latest_month_result.get("max_month"):
        return {
            "tool_name": "get_fund_portfolio_data",
            "success": False,
            "user_id": user_id,
            "error": "펀드 포트폴리오 데이터가 없습니다.",
            "data": []
        }
        
    target_month = latest_month_result["max_month"]
    
    # 2. 해당 월의 데이터 조회
    query = """
        SELECT * FROM monthly_fund_portfolio_snapshot 
        WHERE user_id = :uid AND year_and_month = :month
    """
    data = _safe_execute_query(query, {"uid": user_id, "month": target_month}, fetch_many=True)

    logger.info(f"[get_fund_portfolio_data] user_id: {user_id}, target_month: {target_month}, Data Count: {len(data) if data else 0}")
    
    return {
        "tool_name": "get_fund_portfolio_data",
        "success": True,
        "user_id": user_id,
        "base_month": target_month,
        "data": data if data else []
    }