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
# 🎯 [수정된 함수]: 보고서 월에 해당하는 정책 파일을 찾습니다.
# ------------------------------------------------------------------
def _find_policy_file_for_report(report_date_str: str) -> Optional[str]:
    """
    보고서 날짜(YYYY-MM-DD)를 기준으로, 해당 월에 반영해야 할 정책 파일을 찾습니다.
    (정책 발표일 다음 달 1일이 보고서 작성일인 경우 해당 정책 파일을 선택)
    """
    try:
        # 보고서가 발행되는 월의 1일 (예: 2024-12-01)
        report_month_start = datetime.strptime(report_date_str, "%Y-%m-%d").replace(day=1)

        # 정책 파일 날짜 목록을 역순으로 순회 (최신 정책부터 확인)
        for policy_date_str in sorted(POLICY_FILE_DATES, reverse=True):
            policy_date = datetime.strptime(policy_date_str, "%Y%m%d").date()
            
            # 정책 발표일의 다음 달 1일 (정책 변동 사항이 반영될 목표 보고서 월)
            policy_effective_month_start = (datetime(policy_date.year, policy_date.month, 1) + relativedelta(months=1))
            
            # 만약 정책의 유효 시작 월이 현재 처리 중인 보고서 월과 같다면, 이 정책을 사용합니다.
            if policy_effective_month_start == report_month_start:
                filename = f"{policy_date_str}_policy.pdf"
                full_path = os.path.join(POLICY_DIR, filename)
                
                if os.path.exists(full_path):
                    logger.info(f"RAG: {report_date_str[:7]} 보고서에 {filename} 정책 파일 지정됨.")
                    return full_path
                else:
                    logger.warning(f"RAG: 지정된 정책 파일({filename})이 디렉토리에 없습니다.")
                    return None
        
        logger.info(f"RAG: {report_date_str[:7]} 보고서에 반영할 정책 파일을 찾지 못했습니다. (해당 월은 정책 변동 없음)")
        return None
        
    except Exception as e:
        logger.error(f"정책 파일 결정 중 오류 발생: {e}")
        return None


# ------------------------------------------------------------------
# 🎯 [신규 TOOL 0] 벡터 DB 재구축 및 업데이트
# ------------------------------------------------------------------
# @router.post(
#     "/rebuild_vector_db",
#     summary="정책 문서를 기반으로 FAISS 벡터 DB를 재구축",
#     operation_id="rebuild_vector_db_tool",
#     description="data/policy_documents 폴더의 모든 PDF를 읽어 벡터 DB를 완전히 새로 구축합니다.",
#     response_model=dict,
# )
# async def api_rebuild_vector_db() -> dict:
#     """PDF 파일을 로드, 분할, 임베딩하여 FAISS 벡터 DB를 구축합니다."""
    
#     logger.info(f"--- RAG 벡터 데이터베이스 구축 시작 (Model: {HF_EMBEDDING_MODEL}) ---")
    
#     if not os.path.exists(POLICY_DIR):
#         error_msg = f"❌ '{POLICY_DIR}' 폴더가 존재하지 않습니다. data/policy_documents 폴더를 확인해주세요."
#         logger.error(error_msg)
#         return {"tool_name": "rebuild_vector_db_tool", "success": False, "error": error_msg}

#     file_paths = glob.glob(os.path.join(POLICY_DIR, '*.pdf'))
#     if not file_paths:
#         info_msg = f"✅ policy_documents 폴더에 PDF 파일이 없습니다. 문서를 추가해 주세요."
#         logger.info(info_msg)
#         return {"tool_name": "rebuild_vector_db_tool", "success": True, "message": info_msg}

#     documents = []
#     for file_path in file_paths:
#         loader = PyPDFLoader(file_path)
#         documents.extend(loader.load())

#     custom_separators = [
#         r"\n제[0-9]{1,3}장\s",
#         r"\n제[0-9]{1,3}조\s",
#         r"\n[가-힣\d]\.\s?",
#         r"\n\([가-힣\d]{1,2}\)\s?",
#         r"\n",                           
#         " ",
#         ""
#     ]
    
#     text_splitter = RecursiveCharacterTextSplitter(
#         chunk_size=1000,  
#         chunk_overlap=50, 
#         separators=custom_separators,
#         keep_separator=True
#     )
#     texts = text_splitter.split_documents(documents)
#     logger.info(f"➡️ 총 {len(documents)}개 문서에서 {len(texts)}개의 텍스트 청크 생성 완료.")
    
#     if not HUGGINGFACEHUB_API_TOKEN:
#         error_msg = "❌ HUGGINGFACEHUB_API_TOKEN 환경 변수가 설정되지 않았습니다."
#         logger.error(error_msg)
#         return {"tool_name": "rebuild_vector_db_tool", "success": False, "error": error_msg}
    
#     try:
#         embeddings = HuggingFaceEndpointEmbeddings(
#             model=HF_EMBEDDING_MODEL, 
#             huggingfacehub_api_token=HUGGINGFACEHUB_API_TOKEN,
#         )
#     except Exception as e:
#         error_msg = f"🚨 임베딩 모델 로드 실패: {e}"
#         logger.error(error_msg)
#         return {"tool_name": "rebuild_vector_db_tool", "success": False, "error": error_msg}

#     logger.info(f"💾 FAISS 벡터 DB 생성 중... ({len(texts)}개 청크)")
#     batch_size = 32
#     sleep_time = 3
    
#     if not texts:
#         return {"tool_name": "rebuild_vector_db_tool", "success": True, "message": "경고: 분할된 텍스트 청크가 없어 DB 구축을 건너뜁니다."}
        
#     first_batch = texts[:batch_size]
#     remaining_texts = texts[batch_size:]

#     try:
#         db = FAISS.from_documents(first_batch, embeddings)
#     except Exception as e:
#         error_msg = f"🚨 DB 초기 생성 실패: {e}"
#         logger.error(error_msg)
#         return {"tool_name": "rebuild_vector_db_tool", "success": False, "error": error_msg}

#     total_processed = len(first_batch)
    
#     for i in range(0, len(remaining_texts), batch_size):
#         batch = remaining_texts[i:i + batch_size]
#         logger.info(f"   -- API 요청 지연 ({sleep_time}초 대기) --")
#         time.sleep(sleep_time)
        
#         try:
#             db.add_documents(batch)
#             total_processed += len(batch)
#             logger.info(f"   -> {total_processed} / {len(texts)}개 청크 추가 완료.")
#         except Exception as e:
#             error_msg = f"🚨 임베딩 오류 발생: {e}. DB 구축을 중단합니다."
#             logger.error(error_msg)
#             return {"tool_name": "rebuild_vector_db_tool", "success": False, "error": error_msg}

#     Path(VECTOR_DB_PATH).mkdir(parents=True, exist_ok=True)
#     db.save_local(VECTOR_DB_PATH) 

#     info_msg = f"--- ✅ 벡터 DB 구축 완료 --- (저장 경로: {VECTOR_DB_PATH})"
#     logger.info(info_msg)
#     return {"tool_name": "rebuild_vector_db_tool", "success": True, "message": info_msg}


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
    description="두 달치 소비 데이터(DataFrame Records)를 받아 총 지출, Top 5 카테고리를 비교 분석하고, 군집 별명과 조언을 LLM을 통해 생성합니다.",
    response_model=dict,
)
async def analyze_user_spending(
    consume_records: List[Dict[str, Any]] = Body(..., embed=True),
    member_data: Dict[str, Any] = Body(..., embed=False)
) -> dict:
    """소비 데이터를 기반으로 분석 데이터를 생성합니다. (LLM 호출 제거 - Agent가 처리)"""

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
        
        # Top 5 카테고리 추출 (분석 텍스트용 - 여전히 대분류 기준이 좋을 수 있으나, 일관성을 위해 target_cols 사용)
        # 만약 분석 텍스트는 대분류로 유지하고 싶다면 cat1_cols를 별도로 구해야 함.
        # 여기서는 차트와 일관되게 소분류가 있으면 소분류 Top 5를 사용하도록 변경함.
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
        
        # consume_analysis_summary 구성 (Agent가 사용할 데이터)
        consume_analysis_summary = {
            'latest_total_spend': int(total_spend_latest),
            'previous_total_spend': int(total_spend_prev),
            'spend_diff': int(diff),
            'change_rate': round(change_rate, 2),
            'total_change_diff': change_text,
            'top_5_categories': [col.replace('CAT1_', '') for col in latest_cats.index],
            'top_5_amounts': [int(latest_cats[col]) for col in latest_cats.index],
            'member_info': member_data
        }
        
        return {
            "tool_name": "analyze_user_spending_tool", 
            "success": True, 
            "consume_analysis_summary": consume_analysis_summary,
            "spend_chart_json": spend_chart_json
        }

    except Exception as e:
        logger.error(f"소비 분석 오류: {e}")
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
# 독립 Tool 3: 정책 변동 자동 비교 및 보고서 생성 툴 (🌟 수정 완료)
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
    
    # 🎯 [핵심 수정]: 보고서 월에 해당하는 정책 파일을 찾습니다. (새로운 로직 반영)
    LATEST_POLICY_SOURCE = _find_policy_file_for_report(report_month_str)
    
    if not LATEST_POLICY_SOURCE:
        # 정책 파일이 없으면 변동 없음으로 간주
        report_month = datetime.strptime(report_month_str, "%Y-%m-%d").date().strftime('%Y년 %m월')
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": True, 
            "policy_changes": [],
            "report_month": report_month,
            "message": f"{report_month} 보고서 기준, 해당 월에 반영할 정책 변동 사항이 없습니다."
        }
        
    REQUIRED_SOURCES = [LATEST_POLICY_SOURCE] 
    
    # 1. 📅 날짜 체크 및 초기 설정
    try:
        report_month = datetime.strptime(report_month_str, "%Y-%m-%d").date()
        
        # 최신 정책 파일 날짜 추출
        file_name = Path(LATEST_POLICY_SOURCE).name 
        latest_policy_date_str = file_name.split('_')[0] # 'YYYYMMDD' 추출
        latest_policy_date = datetime.strptime(latest_policy_date_str, "%Y%m%d").date()
        
    except ValueError:
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": False, 
            "policy_changes": [],
            "error": "보고서 월 형식 오류"
        }
    
    # ----------------------------------------------------------------------
    # 2. ⚙️ 정책 섹션별로 RAG 검색 실행 (지정된 파일만 대상)
    # ----------------------------------------------------------------------
    full_context_list = []
    K_SEARCH = 15  # 각 섹션당 검색할 청크 수 (7 -> 15로 증가)
 
    
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
    
    # 디버깅: combined_context의 일부를 출력
    logger.info(f"RAG: combined_context 길이: {len(combined_context)} 문자")
    logger.info(f"RAG: combined_context 샘플 (처음 500자):\n{combined_context[:500]}")
    
    # 3. 📝 정규표현식을 이용해 RAG 컨텍스트에서 마커 포함 구문 추출
    # 정책 파일 날짜를 target_date로 전달하여 해당 날짜의 변경사항만 필터링
    target_policy_date = latest_policy_date.strftime("%Y-%m-%d")
    structured_changes_raw = _find_policies_by_marker_regex(combined_context, target_date=target_policy_date)
    
    # 4. 🎯 최종 파이썬 필터링
    # 🚨 [수정]: 정책 파일이 이미 선택되었으므로, 해당 파일 내 모든 신설/개정 사항을 사용합니다.
    structured_changes = structured_changes_raw
    
    # 5. 🧩 추출 결과 처리 (변동 사항이 없는 경우)
    if not structured_changes:
        # 정책 파일은 있었으나, 해당 파일에서 마커를 포함한 정책 변동 사항이 추출되지 않은 경우
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": True, 
            "policy_changes": [],
            "report_month": report_month.strftime('%Y년 %m월'),
            "policy_date": latest_policy_date.strftime('%Y년 %m월 %d일'),
            "message": f"{report_month.strftime('%Y년 %m월')} 보고서 기준, 최신 정책 문서({latest_policy_date.strftime('%Y년 %m월 %d일')})에 신설 또는 개정된 정책 변동 사항은 확인되지 않았습니다."
        }

    # 6. 🎯 최종 아웃풋 구성 (Agent가 분석 보고서 생성)
    return {
        "tool_name": "check_and_report_policy_changes_tool", 
        "success": True, 
        "policy_changes": structured_changes,
        "report_month": report_month.strftime('%Y년 %m월'),
        "policy_date": latest_policy_date.strftime('%Y년 %m월 %d일')
    }


# ==============================================================================
# 독립 Tool 4: 손익/진척도 분석 (완성)
# ==============================================================================
@router.post(
    "/analyze_investment_profit",
    summary="투자 상품 손익/진척도 분석",
    operation_id="analyze_investment_profit_tool", 
    description="예금, 적금, 펀드의 수익률과 진척도를 분석하고 LLM을 통해 조언을 생성합니다.",
    response_model=dict,
)
async def api_analyze_investment_profit(products: List[Dict[str, Any]] = Body(..., embed=True)) -> dict:
    """보유 상품 목록을 기반으로 손익 데이터를 계산합니다. (LLM 호출 제거 - Agent가 처리)"""
    
    if not products:
        return {
            "tool_name": "analyze_investment_profit_tool", 
            "success": True, 
            "total_principal": 0,
            "total_valuation": 0,
            "net_profit": 0,
            "profit_rate": 0.0,
            "products_count": 0,
            "message": "현재 보유 중인 투자 상품이 없어 분석을 건너킵니다."
        }

    total_principal = 0
    total_valuation = 0
    
    for p in products:
        principal = p.get('total_principal', 0) or 0
        valuation = p.get('current_valuation', 0) or 0
        total_principal += principal
        total_valuation += valuation 

    net_profit = total_valuation - total_principal
    profit_rate = (net_profit / total_principal) * 100 if total_principal else 0
    
    return {
        "tool_name": "analyze_investment_profit_tool", 
        "success": True, 
        "total_principal": int(total_principal),
        "total_valuation": int(total_valuation),
        "net_profit": int(net_profit),
        "profit_rate": round(profit_rate, 2),
        "products_count": len(products)
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
        
        current_value = int(current_value)
        previous_value = int(previous_value)

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