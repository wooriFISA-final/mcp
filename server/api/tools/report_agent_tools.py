import os
import logging
import pandas as pd
import json
import re 
import time 
import glob  
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Body
from datetime import datetime, date
from dateutil.relativedelta import relativedelta 
from dotenv import load_dotenv, find_dotenv, dotenv_values
from pathlib import Path
from langchain_huggingface import HuggingFaceEndpointEmbeddings 
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import create_engine, text
from decimal import Decimal


# ------------------------------------------------------------------
# 🎯 [Environment Cleanup Function] RAG 연결 오염 변수 초기화
# ------------------------------------------------------------------
def _cleanup_rag_env():
    """Hugging Face Endpoint 충돌을 유발할 수 있는 환경 변수를 초기화합니다."""
    if 'HUGGINGFACE_API_URL' in os.environ:
        del os.environ['HUGGINGFACE_API_URL']
        logging.warning("RAG: 환경 변수 HUGGINGFACE_API_URL 강제 제거됨.")
    if 'HF_ENDPOINT' in os.environ:
        del os.environ['HF_ENDPOINT']
        logging.warning("RAG: 환경 변수 HF_ENDPOINT 강제 제거됨.")

# 🎯 [ENV 로드]
load_dotenv(find_dotenv(usecwd=True, raise_error_if_not_found=False) or find_dotenv(usecwd=True) or find_dotenv("..")) 

# 🎯 [ENV 변수] 설정 전에 환경 변수 정리 함수 호출
_cleanup_rag_env() 

# 🎯 [ENV 파일 값 직접 로드]: 셸 환경 변수와의 충돌을 막기 위해 파일 내용만 다시 읽어옵니다.
ENV_VALUES = dotenv_values(find_dotenv(usecwd=True, raise_error_if_not_found=False) or find_dotenv(usecwd=True) or find_dotenv(".."))

# 🎯 [요청 경로 반영]
from server.schemas.report_schema import (
    AnalyzeSpendingInput, AnalyzeSpendingOutput, 
    FinalSummaryInput, FinalSummaryOutput, 
    ToolSkippedOutput, PolicyRAGSearchInput, PolicyRAGSearchOutput 
)


logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 🎯 [ENV 변수] 설정 (ENV_VALUES 딕셔너리에서 직접 로드) - 충돌 방지 목적
# ------------------------------------------------------------------
# RAG 및 정책 검색에 필요한 환경 변수만 로드합니다.
HF_EMBEDDING_MODEL = ENV_VALUES.get("HF_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B")
VECTOR_DB_PATH = ENV_VALUES.get("VECTOR_DB_PATH", './data/faiss_index')
HUGGINGFACEHUB_API_TOKEN = ENV_VALUES.get("HUGGINGFACEHUB_API_TOKEN")



# 🚨 [추가] 정책 문서 디렉토리 경로
POLICY_DIR = "./data/policy_documents"


router = APIRouter(
    prefix="/report_processing",
    tags=["Report Processing Tools"] 
    
)

# ------------------------------------------------------------------
# 🎯 [DB 연결 설정] Agent Tools에서 직접 DB 조회
# ------------------------------------------------------------------
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

engine = None
if DB_USER and DB_PASSWORD and DB_HOST and DB_NAME:
    try:
        engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")
        logger.info("✅ Report Agent Tools DB Engine 생성 완료")
    except Exception as e:
        logger.error(f"❌ DB Engine 생성 실패: {e}")

def _execute_query(query: str, params: Dict[str, Any], fetch_many: bool = False) -> List[Dict[str, Any]] | Dict[str, Any] | None:
    """DB 쿼리를 안전하게 실행하는 내부 유틸리티."""
    if engine is None: 
        logger.warning("DB Engine이 연결되지 않았습니다.")
        return None if not fetch_many else []
    try:
        with engine.connect() as conn:
            result = conn.execute(text(query), params).mappings().all()
            
            processed_results = []
            for row in result:
                processed_row = dict(row)
                for key, value in processed_row.items():
                    if isinstance(value, (date, datetime)):
                        processed_row[key] = value.strftime("%Y-%m-%d")
                    elif isinstance(value, Decimal):
                        processed_row[key] = float(value) 
                processed_results.append(processed_row)
            
            if fetch_many: 
                return processed_results
            else: 
                return processed_results[0] if processed_results else None
    except Exception as e:
        logger.error(f"DB 쿼리 실행 오류: {e}", exc_info=True)
        return None if not fetch_many else []

# ------------------------------------------------------------------
# 🎯 [새로운 상수 정의] 정책 파일과 적용 월의 규칙 매핑 (YYYYMMDD_policy.pdf)
# ------------------------------------------------------------------
# 정책 배포일(YYYYMMDD) 목록. 이 날짜의 정책은 다음 달 1일 보고서에 반영되어야 합니다.
POLICY_FILE_DATES = [
    "20250305",  # 2025년 4월 보고서에 반영
    "20241224",  # 2025년 1월 보고서에 반영
    "20240724",  # 2024년 8월 보고서에 반영
    "20240626",  # 2024년 7월 보고서에 반영
    "20240430",  # 2024년 5월 보고서에 반영
    "20230830",  # 2023년 9월 보고서에 반영
    "20230621",  # 2023년 7월 보고서에 반영
    "20230302",  # 2023년 4월 보고서에 반영
]

# ------------------------------------------------------------------
# 🎯 [핵심 함수] 보고서 월에 해당하는 정책 파일 찾기
# ------------------------------------------------------------------
def _find_policy_file_for_report(report_date_str: str) -> Optional[str]:
    """
    보고서 날짜(YYYY-MM-DD 또는 YYYY-MM)를 기준으로, 해당 월에 반영해야 할 정책 파일을 찾습니다.
    
    로직:
    - 보고서 제공일(create_at)이 2024-03-01이면 → 2024년 2월 리포트
    - 2024년 2월에 발표된 정책을 포함
    - 즉, report_date_str의 전월(2월)에 발표된 정책을 찾음
    """
    try:
        # 🔧 수정: 입력 형식에 관계없이 YYYY-MM-DD 형태로 변환
        if len(report_date_str) == 7:  # YYYY-MM 형식
            report_date_str = report_date_str + "-01"
            
        # 보고서 제공일 (예: 2024-03-01)
        report_delivery_date = datetime.strptime(report_date_str[:10], "%Y-%m-%d").replace(day=1)
        
        # 🎯 [핵심 수정]: 보고서 대상 월 = 제공일의 전월 (예: 2024-02)
        report_target_month = report_delivery_date - relativedelta(months=1)

        # 정책 파일 날짜 목록을 역순으로 순회 (최신 정책부터 확인)
        for policy_date_str in sorted(POLICY_FILE_DATES, reverse=True):
            policy_date = datetime.strptime(policy_date_str, "%Y%m%d").date()
            
            # 🎯 [핵심 수정]: 정책 발표 월 (예: 2024-02)
            policy_month = datetime(policy_date.year, policy_date.month, 1)
            
            # 정책 발표 월 == 보고서 대상 월이면 해당 정책 사용
            if policy_month == report_target_month:
                filename = f"{policy_date_str}_policy.pdf"
                full_path = os.path.join(POLICY_DIR, filename)
                
                if os.path.exists(full_path):
                    logger.info(f"RAG: {report_delivery_date.strftime('%Y-%m')} 제공 리포트(대상월: {report_target_month.strftime('%Y-%m')})에 {filename} 정책 파일 지정됨.")
                    return full_path
                else:
                    logger.warning(f"RAG: 지정된 정책 파일({filename})이 디렉토리에 없습니다. ({full_path})")
                    return None
        
        logger.info(f"RAG: {report_delivery_date.strftime('%Y-%m')} 제공 리포트(대상월: {report_target_month.strftime('%Y-%m')})에 반영할 정책 파일을 찾지 못했습니다.")
        return None
        
    except Exception as e:
        logger.error(f"정책 파일 결정 중 오류 발생: {e}", exc_info=True)
        return None


# ------------------------------------------------------------------
# 🎯 [신규 TOOL 0] 벡터 DB 재구축 및 업데이트
# ------------------------------------------------------------------
# ... (주석 처리된 api_rebuild_vector_db 함수 유지) ...


# ------------------------------------------------------------------
# 🎯 [정책 섹션 정의] RAG 검색 시 사용할 섹션 목록
# ------------------------------------------------------------------
# 1장은 문서 전체 개정 이력만 있으므로 제외하고, 2장부터 검색
POLICY_SECTIONS_TO_CHECK = [
    "2장 나. 주택담보대출 담보인정비율(LTV) 변동 및 특례 적용",
    "3장 다. 주택담보대출 총부채상환비율(DTI) 적용 및 배제 기준",
    "4장 라. 고액 가계대출 DSR 적용 기준 및 예외 사항",
    "5장 마. 주택관련 담보대출 취급 관련 유의사항 및 특례 대출 신설"
]


# ------------------------------------------------------------------
# 🎯 RAG 검색 유틸리티 함수 (단일 파일 필터링 가능하도록 수정)
# ------------------------------------------------------------------
def _rag_similarity_search(query: str, k: int = 5, required_sources: Optional[List[str]] = None) -> str:
    """FAISS DB를 로드하여 쿼리를 검색하고 결과를 텍스트로 반환합니다. 지정된 소스 파일 목록에서만 청크를 가져옵니다."""

    if not HUGGINGFACEHUB_API_TOKEN:
        return "🚨 RAG 검색 실패: HUGGINGFACEHUB_API_TOKEN이 설정되지 않았습니다."
        
    current_model = HF_EMBEDDING_MODEL 
    logger.info(f"RAG: 임베딩 모델 {current_model} 사용.")

    try:
        embeddings = HuggingFaceEndpointEmbeddings(
            model=current_model,
            huggingfacehub_api_token=HUGGINGFACEHUB_API_TOKEN,
        )
        
        db = FAISS.load_local(VECTOR_DB_PATH, embeddings, allow_dangerous_deserialization=True)
        
        found_chunks = db.similarity_search(query, k=k * 4) 
        
        logger.info(f"RAG: 검색어 '{query}'로 {len(found_chunks)}개 청크 발견 (required_sources: {required_sources})")
        
        context = []
        filtered_count = 0
        
        for idx, chunk in enumerate(found_chunks):
            source = chunk.metadata.get("source", "출처 미상")
            
            # 디버깅: 처음 5개 청크의 source 출력
            if idx < 5:
                logger.info(f"RAG: 청크 {idx} - source: '{source}'")
            
            is_valid_source = not required_sources or any(req_src in source for req_src in required_sources)
            
            if not is_valid_source:
                if idx < 5:
                    logger.info(f"RAG: 청크 {idx} - 필터링됨 (source 불일치)")
                continue

            if filtered_count < k:
                context.append(f"[출처: {source}]\n{chunk.page_content}")
                filtered_count += 1
                if idx < 5:
                    logger.info(f"RAG: 청크 {idx} - 포함됨!")
            else:
                break

        logger.info(f"RAG: 최종 {filtered_count}개 청크 반환 (목표: {k}개)")
        
        if not context:
            source_info = f"문서 목록: {required_sources}" if required_sources else "모든 문서"
            return f"🚨 RAG 검색 실패: 검색어 '{query}'에 대해 {source_info}에서 유효한 청크를 찾지 못했습니다."
            
        return "\n---\n".join(context)
    
    except Exception as e:
        logger.error(f"RAG 검색 시스템 오류: {e}", exc_info=True)
        return f"🚨 RAG 검색 실패: {type(e).__name__} - {e}"


# ------------------------------------------------------------------
# 🎯 [신규 함수]: 정규표현식으로 마커 포함 구문 100% 탐지 (개정/신설 유연성 강화)
# ------------------------------------------------------------------
def _find_policies_by_marker_regex(context: str, target_date: Optional[str] = None) -> List[Dict[str, str]]:
    """
    RAG 컨텍스트 내에서 <신설 YYYY.M.D.> 또는 <개정 YYYY.M.D.> 마커를 포함한 정책 구문을 정규표현식으로 추출 및 정규화.
    
    Args:
        context: RAG 검색 결과 텍스트
        target_date: 필터링할 목표 날짜 (YYYY-MM-DD 형식). 지정 시 해당 날짜의 변경사항만 반환
    """
    
    context_clean = re.sub(r'\[출처:.*?\.pdf\]', '', context, flags=re.DOTALL)
    context_clean = re.sub(r'---\n', '', context_clean, flags=re.DOTALL)
    
    # 정규표현식: 조항 번호 + 내용 + <신설/개정 날짜> 패턴 찾기
    # 예: "21.(임차보증금반환목적...) <개정 2024.7.24., 2024.12.24.>"
    # <별표6><신설...> 같은 문서 전체 개정 이력은 제외
    regex = r"(\d{1,3}\.[\s\S]{10,1000}?)<\s*(신설|개정)\s*([^>]+)>"
    
    matches = re.findall(regex, context_clean, re.DOTALL) 
    
    logger.info(f"RAG: 정규표현식 매칭 결과 {len(matches)}개 발견")
    
    extracted_changes = []
    
    for policy_text, change_type, dates_str in matches:
        # <별표X> 패턴이 포함된 경우 제외
        if re.search(r'<별표\d+>', policy_text):
            logger.info(f"RAG: <별표> 패턴 발견으로 제외")
            continue
        
        # 날짜 문자열에서 모든 날짜 추출
        date_pattern = r'(\d{4})\.(\d{1,2})\.(\d{1,2})'
        all_dates = re.findall(date_pattern, dates_str)
        
        if not all_dates:
            logger.warning(f"RAG: 날짜 파싱 실패 - dates_str: '{dates_str}'")
            continue
        
        # 가장 최신 날짜 찾기 (마지막 날짜가 보통 최신)
        latest_date_tuple = all_dates[-1]
        year, month, day = latest_date_tuple
        
        try:
            effective_date = datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            logger.warning(f"RAG: 날짜 변환 실패 - year: {year}, month: {month}, day: {day}")
            continue
        
        logger.info(f"RAG: 발견된 변경사항 - 날짜: {effective_date}, 타입: {change_type}, 조항: {policy_text[:50]}...")
        
        # target_date가 지정된 경우, 해당 날짜와 일치하는 것만 포함
        if target_date:
            if effective_date != target_date:
                logger.info(f"RAG: 날짜 불일치로 제외 - effective_date: {effective_date}, target_date: {target_date}")
                continue
            else:
                logger.info(f"RAG: 날짜 일치! 포함 - {effective_date}")
        
        # 텍스트 정규화
        normalized_text = policy_text.strip()
        normalized_text = re.sub(r'\s{2,}', ' ', normalized_text)
        
        # 마커 추가
        full_text = f"{normalized_text} <{change_type} {dates_str}>"
        
        extracted_changes.append({
            "effective_date": effective_date,
            "policy_text": full_text
        })

    logger.info(f"RAG: 최종 추출된 변경사항 {len(extracted_changes)}개 (target_date: {target_date})")
    return extracted_changes



# ------------------------------------------------------------------
# 🎯 [REMOVED]: _generate_final_report_from_structured_data
# This function has been removed. The Agent will now handle policy report generation.
# ------------------------------------------------------------------


# ==============================================================================
# 독립 Tool 1: 소비 데이터 분석 및 군집 생성
# ==============================================================================
@router.post(
    "/analyze_user_spending",
    summary="월별 소비 데이터 비교 분석 및 군집 생성",
    operation_id="analyze_user_spending_tool", 
    description="두 달치 소비 데이터(DataFrame Records)를 받아 총 지출, Top 5 카테고리를 비교 분석합니다. Agent가 이 데이터를 보고 별명과 분석 텍스트를 생성합니다.",
    response_model=dict,
)
async def analyze_user_spending(
    consume_records: List[Dict[str, Any]] = Body(..., embed=True),
    member_data: Dict[str, Any] = Body(..., embed=False)
) -> dict:
    """소비 데이터를 기반으로 분석 데이터를 생성합니다. Agent가 이 데이터로 별명과 리포트를 작성합니다."""

    if not consume_records or len(consume_records) < 2:
        error_msg = "비교 분석을 위한 최소 2개월 데이터 부족" if consume_records else "분석할 소비 데이터가 존재하지 않아 건너뜁니다."
        return {
            "tool_name": "analyze_user_spending_tool", 
            "success": False, 
            "error": error_msg,
            "consume_analysis_summary": {},
            "spend_chart_json": json.dumps({})
        }
    
    try:
        # 데이터프레임으로 변환 및 정렬
        df_consume = pd.DataFrame(consume_records)
        
        # 🚨 [Fix] Handle 'YYYY_MM' format from DB by replacing '_' with '-'
        if 'year_and_month' in df_consume.columns:
            df_consume['year_and_month'] = df_consume['year_and_month'].astype(str).str.replace('_', '-')
            
        df_consume['year_and_month'] = pd.to_datetime(df_consume['year_and_month'])
        df_consume = df_consume.sort_values(by='year_and_month', ascending=False)
        
        latest_data = df_consume.iloc[0] # 최신 월 데이터
        previous_data = df_consume.iloc[1]

        total_spend_latest = latest_data.get('total_spend', 0) or 0
        total_spend_prev = previous_data.get('total_spend', 0) or 0
        diff = total_spend_latest - total_spend_prev
        change_rate = (diff / total_spend_prev) * 100 if total_spend_prev else 0
        change_text = f"{diff:+,}원 ({change_rate:.2f}%) 변동"

        # 🚨 [수정] 소분류(CAT2) 우선 사용, 없으면 대분류(CAT1) 사용
        cat2_cols = [col for col in latest_data.index if col.startswith('CAT2_')]
        target_cols = cat2_cols if cat2_cols else [col for col in latest_data.index if col.startswith('CAT1_')]
        prefix = 'CAT2_' if cat2_cols else 'CAT1_'
        
        # Top 5 카테고리 추출
        latest_cats = df_consume.iloc[0][target_cols].sort_values(ascending=False).head(5) 
        
        # spend_chart_json을 위한 전체 카테고리별 금액 계산
        chart_data_list = []
        for col in target_cols:
            amount = latest_data.get(col, 0) or 0
            if amount > 0:
                # 라벨 정제: 접두사 제거 및 언더바를 공백/슬래시로 변환
                label = col.replace(prefix, '').replace('_', ' ').replace(' ', '/') 
                chart_data_list.append({
                    "category": label,
                    "amount": int(amount)
                })
        spend_chart_json = json.dumps(chart_data_list, ensure_ascii=False)
        
        # Top 5 카테고리 이름과 금액
        top_5_categories = [col.replace(prefix, '').replace('_', ' ') for col in latest_cats.index]
        top_5_amounts = [int(latest_cats[col]) for col in latest_cats.index]
        
        # consume_analysis_summary 구성 (Agent가 사용할 데이터)
        consume_analysis_summary = {
            'latest_total_spend': int(total_spend_latest),
            'previous_total_spend': int(total_spend_prev),
            'spend_diff': int(diff),
            'change_rate': round(change_rate, 2),
            'total_change_diff': change_text,
            'top_5_categories': top_5_categories,
            'top_5_amounts': top_5_amounts,
            'member_info': member_data
        }
        
        return {
            "tool_name": "analyze_user_spending_tool", 
            "success": True, 
            "consume_analysis_summary": consume_analysis_summary,
            "spend_chart_json": spend_chart_json
        }

    except Exception as e:
        logger.error(f"소비 분석 오류: {e}", exc_info=True)
        return {"tool_name": "analyze_user_spending_tool", "success": False, "error": str(e)}

    
# ==============================================================================
# 독립 Tool 2: 최종 3줄 요약 LLM Tool
# ==============================================================================
@router.post(
    "/generate_final_summary",
    summary="최종 보고서 3줄 요약 생성",
    operation_id="generate_final_summary_llm", 
    description="통합 보고서 본문을 받아 핵심 내용을 3줄로 간결하게 요약합니다.",
    response_model=dict,
)
async def api_generate_final_summary(report_content: str = Body(..., embed=True)) -> dict:
    """
    [DEPRECATED] This tool now returns the report content as-is. 
    The Agent will handle summarization internally instead of calling this tool.
    """
    
    return {
        "tool_name": "generate_final_summary_llm", 
        "success": True, 
        "report_content": report_content,
        "message": "이 도구는 더 이상 LLM을 호출하지 않습니다. Agent가 직접 요약을 생성합니다."
    }



# ==============================================================================
# 독립 Tool 3: 정책 변동 자동 비교 및 보고서 생성 툴 (🌟 최종 수정)
# ==============================================================================
@router.post(
    "/check_and_report_policy_changes",
    summary="매월 자동 정책 변동 비교 및 보고서 생성",
    operation_id="check_and_report_policy_changes_tool", 
    description="사용자 입력 없이, 정의된 정책 섹션별로 RAG 검색을 실행하여 변동 사항을 확인하고 구조화된 최종 보고서를 생성합니다.",
    response_model=dict,
)
async def api_check_policy_changes(
    report_month_str: str = Body(..., embed=True) 
) -> dict:
    """매월 정기 보고서 생성을 위해 정책 변동 사항을 자동으로 비교합니다. (LLM 호출 제거 - Agent가 처리)"""
    
    # 1. 📅 보고서 월 정규화 (YYYY-MM 또는 YYYY-MM-DD 모두 처리)
    try:
        if len(report_month_str) == 7 and report_month_str.count('-') == 1:  # "YYYY-MM" 형식
            # YYYY-MM 형식일 경우, 날짜 객체로 변환하기 위해 '-01'을 추가
            report_month_dt = datetime.strptime(report_month_str + "-01", "%Y-%m-%d")
        elif len(report_month_str) >= 10 and report_month_str.count('-') >= 2: # "YYYY-MM-DD" 이상 형식
            # YYYY-MM-DD 형식으로 바로 파싱
            report_month_dt = datetime.strptime(report_month_str[:10], "%Y-%m-%d")
        else:
            raise ValueError(f"지원하지 않는 날짜 형식입니다: {report_month_str}")
            
        # 사용자에게 보여줄 'YYYY년 MM월' 형식
        report_month_display = report_month_dt.strftime('%Y년 %m월')
        report_month_date = report_month_dt.date() # 비교용 Date 객체
        
        # 🎯 정책 파일을 찾는 함수에 전달할 YYYY-MM-DD 형식
        report_date_for_search = report_month_date.strftime('%Y-%m-%d')
        
    except ValueError as e:
        logger.error(f"보고서 월 파싱 오류: {e}", exc_info=True)
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": False, 
            "policy_changes": [],
            "error": f"보고서 월 형식 오류: 입력된 날짜 '{report_month_str}'을(를) 처리할 수 없습니다."
        }
    
    # 2. 🎯 [핵심 수정]: 보고서 월에 해당하는 정책 파일을 찾습니다.
    # _find_policy_file_for_report는 YYYY-MM-DD 형태를 기대합니다.
    LATEST_POLICY_SOURCE = _find_policy_file_for_report(report_date_for_search)
    
    if not LATEST_POLICY_SOURCE:
        # 정책 파일이 없으면 변동 없음으로 간주
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": True, 
            "policy_changes": [],
            "report_month": report_month_display,
            "message": f"{report_month_display} 보고서 기준, 해당 월에 반영할 정책 변동 사항이 없습니다."
        }
        
    REQUIRED_SOURCES = [LATEST_POLICY_SOURCE] 
    
    # 3. 최신 정책 파일 날짜 추출 및 검증
    try:
        file_name = Path(LATEST_POLICY_SOURCE).name 
        latest_policy_date_str = file_name.split('_')[0] # 'YYYYMMDD' 추출
        latest_policy_date = datetime.strptime(latest_policy_date_str, "%Y%m%d").date()
        
    except Exception as e:
        logger.error(f"정책 파일 이름 파싱 오류: {e}", exc_info=True)
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": False, 
            "policy_changes": [],
            "error": "정책 파일 이름 파싱 중 오류 발생"
        }
    
    # ----------------------------------------------------------------------
    # 4. ⚙️ 정책 섹션별로 RAG 검색 실행 (지정된 파일만 대상)
    # ----------------------------------------------------------------------
    full_context_list = []
    K_SEARCH = 15  # 각 섹션당 검색할 청크 수
 
    
    for section_query in POLICY_SECTIONS_TO_CHECK:
        rag_context = _rag_similarity_search(
            query=section_query, 
            k=K_SEARCH, 
            required_sources=REQUIRED_SOURCES 
        )

        if "🚨 RAG 검색 실패" not in rag_context:
            full_context_list.append(rag_context)
        else:
             return {
                "tool_name": "check_and_report_policy_changes_tool", 
                "success": False, 
                "policy_changes": [],
                "error": rag_context
            }

    combined_context = "\n---\n".join(full_context_list)
    
    # 5. 📝 정규표현식을 이용해 RAG 컨텍스트에서 마커 포함 구문 추출
    # 정책 파일 날짜를 target_date로 전달하여 해당 날짜의 변경사항만 필터링
    target_policy_date = latest_policy_date.strftime("%Y-%m-%d")
    structured_changes = _find_policies_by_marker_regex(combined_context, target_date=target_policy_date)
    
    
    # 6. 🧩 추출 결과 처리 (변동 사항이 없는 경우)
    if not structured_changes:
        # 정책 파일은 있었으나, 해당 파일에서 마커를 포함한 정책 변동 사항이 추출되지 않은 경우
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": True, 
            "policy_changes": [],
            "report_month": report_month_display,
            "policy_date": latest_policy_date.strftime('%Y년 %m월 %d일'),
            "message": f"{report_month_display} 보고서 기준, 최신 정책 문서({latest_policy_date.strftime('%Y년 %m월 %d일')})에 신설 또는 개정된 정책 변동 사항은 확인되지 않았습니다."
        }

    # 7. 🎯 최종 아웃풋 구성 (Agent가 분석 보고서 생성)
    return {
        "tool_name": "check_and_report_policy_changes_tool", 
        "success": True, 
        "policy_changes": structured_changes,
        "report_month": report_month_display,
        "policy_date": latest_policy_date.strftime('%Y년 %m월 %d일')
    }


# ==============================================================================
# 독립 Tool 4: 손익/진척도 분석 (완성)
# ==============================================================================
@router.post(
    "/analyze_investment_profit",
    summary="투자 상품 손익/진척도 분석 + 그래프 데이터 생성",
    operation_id="analyze_investment_profit_tool", 
    description="예금, 적금, 펀드의 수익률과 진척도를 분석하고 그래프 데이터를 생성합니다.",
    response_model=dict,
)
async def api_analyze_investment_profit(
    user_id: int = Body(..., embed=True),
    # products, monthly_data, fund_portfolio_data are now fetched internally
) -> dict:
    """
    보유 상품 목록과 월별 시뮬레이션 데이터를 DB에서 직접 조회하여 손익 데이터 및 그래프 데이터를 계산합니다.
    """
    
    # 1. DB에서 데이터 조회
    # (1) 보유 상품 목록 (my_products)
    products_query = "SELECT * FROM my_products WHERE user_id = :uid"
    products = _execute_query(products_query, {"uid": user_id}, fetch_many=True) or []

    # (2) 월별 시뮬레이션 데이터 (monthly_simulation_report) - 최근 12개월
    monthly_query = """
        SELECT * FROM monthly_simulation_report 
        WHERE user_id = :uid 
        ORDER BY year_and_month ASC
        LIMIT 12
    """
    monthly_data = _execute_query(monthly_query, {"uid": user_id}, fetch_many=True) or []

    # (3) 펀드 포트폴리오 스냅샷 (monthly_fund_portfolio_snapshot) - 최신 월
    latest_month_query = "SELECT MAX(year_and_month) as max_month FROM monthly_fund_portfolio_snapshot WHERE user_id = :uid"
    latest_month_result = _execute_query(latest_month_query, {"uid": user_id}, fetch_many=False)
    
    fund_portfolio_data = []
    if latest_month_result and latest_month_result.get("max_month"):
        target_month = latest_month_result["max_month"]
        fund_query = """
            SELECT * FROM monthly_fund_portfolio_snapshot 
            WHERE user_id = :uid AND year_and_month = :month
        """
        fund_portfolio_data = _execute_query(fund_query, {"uid": user_id, "month": target_month}, fetch_many=True) or []
    
    total_principal = 0
    total_valuation = 0
    
    # 1. 현재 보유 상품 손익 계산 (my_product 테이블 기준)
    if products:
        for p in products:
            # payment_amount: 투자 원금, current_value: 현재 평가액
            principal = p.get('payment_amount', 0) or 0
            valuation = p.get('current_value', 0) or 0
            
            # 문자열일 경우 float 변환
            if isinstance(principal, str): principal = float(principal)
            if isinstance(valuation, str): valuation = float(valuation)
            
            total_principal += principal
            total_valuation += valuation

    net_profit = total_valuation - total_principal
    profit_rate = (net_profit / total_principal) * 100 if total_principal else 0
    
    # 2. 그래프 1: 월별 수익률 추이 (monthly_simulation_report 기반)
    trend_chart_data = []
    if monthly_data:
        for record in monthly_data:
            # total_return_rate는 0.05 처럼 소수점으로 저장됨 -> 100 곱해서 %로 변환
            fund_rate = float(record.get("total_return_rate", 0) or 0) * 100
            
            trend_chart_data.append({
                "month": record.get("year_and_month", ""),
                "deposit_rate": float(record.get("deposit_rate", 0) or 0),
                "savings_rate": float(record.get("savings_rate", 0) or 0),
                "fund_rate": round(fund_rate, 2)
            })
    
    trend_chart_json = json.dumps(trend_chart_data, ensure_ascii=False)
    
    # 3. 그래프 2: 펀드 상품별 손익 (monthly_fund_portfolio_snapshot 기반)
    fund_comparison_data = []
    if fund_portfolio_data:
        for fund in fund_portfolio_data:
            invested = float(fund.get('invested_amount', 0) or 0)
            eval_amt = float(fund.get('eval_amount', 0) or 0)
            profit = eval_amt - invested
            
            fund_comparison_data.append({
                "name": fund.get('fund_product_name', '알 수 없음'),
                "principal": int(invested),
                "valuation": int(eval_amt),
                "profit": int(profit)
            })
            
    fund_comparison_json = json.dumps(fund_comparison_data, ensure_ascii=False)
    
    return {
        "tool_name": "analyze_investment_profit_tool", 
        "success": True, 
        "total_principal": int(total_principal),
        "total_valuation": int(total_valuation),
        "net_profit": int(net_profit),
        "profit_rate": round(profit_rate, 2),
        "products_count": len(products) if products else 0,
        "trend_chart_json": trend_chart_json,
        "fund_comparison_json": fund_comparison_json
    }


# ==============================================================================
# 독립 Tool 5: 사용자 프로필 변동 분석 및 보고서 생성 - 🌟 최종 안정화
# ==============================================================================
@router.post(
    "/analyze_user_profile_changes",
    summary="사용자 개인 지수 변동 분석 및 보고서 생성",
    operation_id="analyze_user_profile_changes_tool",
    description="직전 보고서와 현재 DB에서 조회한 연봉, 부채, 신용 점수를 비교하고, LLM을 통해 변동 보고서를 생성합니다.",
    response_model=dict,
)
async def analyze_user_profile_changes(
    current_data: Dict[str, Any] = Body(..., embed=True),
    previous_data: Dict[str, Any] = Body(..., embed=False)
) -> dict:
    """사용자의 연봉, 부채, 신용 점수 변동 데이터를 계산합니다. (LLM 호출 제거 - Agent가 처리)"""
    
    # 1. 📊 데이터 비교 및 요약
    change_raw_changes = []
    
    # 비교 대상 필드 리스트
    fields_to_compare = [
        ('annual_salary', '연봉'), 
        ('total_debt', '총 부채'), 
        ('credit_score', '신용 점수')
    ]

    is_first_report = all(v == 0 for k, v in previous_data.items() if k in ['annual_salary', 'total_debt', 'credit_score'])
    
    for field, name in fields_to_compare:
        current_value = current_data.get(field, 0) or 0
        previous_value = previous_data.get(field, 0) or 0
        
        # 🚨 Decimal/Float이 섞여 있을 수 있으므로 정수로 안전하게 변환
        current_value = int(float(current_value))
        previous_value = int(float(previous_value))

        diff = current_value - previous_value
        
        # 🚨 [수정]: 첫 보고서가 아닐 때만 0이 아닌 유의미한 변동을 비교
        if not is_first_report and diff != 0:
            change_raw_changes.append(f"{name} 변동: {previous_value:,}원 → {current_value:,}원 ({diff:+,}원)")
        # 🚨 [추가]: 첫 보고서이고, 현재 데이터가 0이 아닌 경우 현재 상태만 기록
        elif is_first_report and current_value != 0:
             change_raw_changes.append(f"최초 기록 {name}: {current_value:,}원")
    
    if not change_raw_changes:
        return {
            "tool_name": "analyze_user_profile_changes_tool",
            "success": True,
            "change_raw_changes": [],
            "is_first_report": is_first_report,
            "message": "직전 보고서 대비 주요 개인 금융 지표(연봉, 부채, 신용 점수)의 변동 사항은 없습니다."
        }
    
    return {
        "tool_name": "analyze_user_profile_changes_tool",
        "success": True,
        "change_raw_changes": change_raw_changes,
        "is_first_report": is_first_report
    }