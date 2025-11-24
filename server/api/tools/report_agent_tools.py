import os
import requests
import logging
import pandas as pd
import json
import re 
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Body
from datetime import datetime, date
from dotenv import load_dotenv, find_dotenv, dotenv_values
from pathlib import Path
from langchain_huggingface import HuggingFaceEndpointEmbeddings 
from langchain_community.vectorstores import FAISS
from pathlib import Path


# ------------------------------------------------------------------
# 🎯 [Environment Cleanup Function] RAG 연결 오염 변수 초기화
# ------------------------------------------------------------------
def _cleanup_rag_env():
    """Hugging Face Endpoint 충돌을 유발할 수 있는 환경 변수를 초기화합니다."""
    if 'HUGGINGFACE_API_URL' in os.environ:
        del os.environ['HUGGINGFACE_API_URL']
        logger.warning("RAG: 환경 변수 HUGGINGFACE_API_URL 강제 제거됨.")
    if 'HF_ENDPOINT' in os.environ:
        del os.environ['HF_ENDPOINT']
        logger.warning("RAG: 환경 변수 HF_ENDPOINT 강제 제거됨.")

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
# os.getenv 대신 ENV_VALUES 딕셔너리에서 직접 값을 가져와 셸 충돌을 방지합니다.
OLLAMA_HOST = ENV_VALUES.get("OLLAMA_HOST", 'http://localhost:11434') 
QWEN_MODEL = ENV_VALUES.get("REPORT_LLM", 'qwen3:8b')

# 🛑 [핵심 설정]: HF_EMBEDDING_MODEL을 Qwen 모델로 설정합니다.
HF_EMBEDDING_MODEL = ENV_VALUES.get("HF_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B")
VECTOR_DB_PATH = ENV_VALUES.get("VECTOR_DB_PATH", '../data/faiss_index')
HUGGINGFACEHUB_API_TOKEN = ENV_VALUES.get("HUGGINGFACEHUB_API_TOKEN")

router = APIRouter(
    prefix="/report_processing",
    tags=["Report Processing Tools"] 
)

# ------------------------------------------------------------------
# 🎯 정책 PDF 구조에 맞춰 RAG 검색을 자동화할 키워드 목록
# ------------------------------------------------------------------
POLICY_SECTIONS_TO_CHECK = [
    "1장 가. 용어의 정의 변경 및 신설 항목",
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
        
    # 🎯 [최종 확인]: HF_EMBEDDING_MODEL 변수의 현재 값을 사용
    current_model = HF_EMBEDDING_MODEL 
    logger.info(f"RAG: 임베딩 모델 {current_model} 사용.")


    try:
        # 🎯 HuggingFaceEndpointEmbeddings 사용
        embeddings = HuggingFaceEndpointEmbeddings(
            model=current_model,
            huggingfacehub_api_token=HUGGINGFACEHUB_API_TOKEN,
        )
        
        db = FAISS.load_local(VECTOR_DB_PATH, embeddings, allow_dangerous_deserialization=True)
        
        # 필터링을 위해 충분히 많은 청크를 가져옵니다.
        # RAG 검색 깊이는 정책 누락 방지를 위해 7로 유지합니다.
        found_chunks = db.similarity_search(query, k=k * 4) 
        
        context = []
        filtered_count = 0
        
        for chunk in found_chunks:
            source = chunk.metadata.get("source", "출처 미상")
            
            # 🎯 [핵심 로직] required_sources 리스트에 해당 source가 포함되는지 확인합니다.
            is_valid_source = not required_sources or any(req_src in source for req_src in required_sources)
            
            if not is_valid_source:
                continue

            # 필터링된 결과만 K개까지 저장
            if filtered_count < k:
                context.append(f"[출처: {source}]\n{chunk.page_content}")
                filtered_count += 1
            else:
                break

        if not context:
            source_info = f"문서 목록: {required_sources}" if required_sources else "모든 문서"
            return f"🚨 RAG 검색 실패: 검색어 '{query}'에 대해 {source_info}에서 유효한 청크를 찾지 못했습니다."
            
        return "\n---\n".join(context)
    
    except Exception as e:
        logger.error(f"RAG 검색 시스템 오류: {e}", exc_info=True)
        return f"🚨 RAG 검색 실패: {type(e).__name__} - {e}"


# ------------------------------------------------------------------
# 🎯 [신규 유틸리티 함수]: 정책 문서 디렉토리에서 최신 파일 경로 찾기
# ------------------------------------------------------------------
def _find_latest_policy_file(base_dir: str) -> Optional[str]:
    """
    지정된 디렉토리에서 'YYYYMMDD_policy.pdf' 패턴을 따르는 파일 중
    날짜가 가장 최신인 파일의 경로를 반환합니다.
    """
    
    # 🚨 [수정]: Path 객체를 사용하여 디렉토리 접근
    policy_dir = Path(base_dir) 
    
    if not policy_dir.is_dir():
        logger.error(f"정책 문서 디렉토리를 찾을 수 없습니다: {base_dir}")
        return None
    
    # 정규표현식: YYYYMMDD_policy.pdf 패턴에 맞고, 날짜 부분을 그룹으로 캡처
    date_file_pattern = re.compile(r'(\d{8})_policy\.pdf$', re.IGNORECASE)
    
    latest_file_info = None # (날짜, 경로) 튜플 저장
    
    # 디렉토리 내 모든 PDF 파일 탐색
    for file_path in policy_dir.glob('*_policy.pdf'):
        match = date_file_pattern.search(file_path.name)
        
        if match:
            file_date_str = match.group(1)
            
            # 가장 큰(최신) 날짜를 찾습니다.
            if latest_file_info is None or file_date_str > latest_file_info[0]:
                # 윈도우/리눅스 환경 모두에서 경로를 올바르게 사용하기 위해 str로 변환
                latest_file_info = (file_date_str, str(file_path)) 

    if latest_file_info:
        logger.info(f"RAG: 가장 최신 정책 파일 발견: {latest_file_info[1]}")
        return latest_file_info[1]
    else:
        logger.warning(f"RAG: {base_dir}에서 유효한 정책 파일을 찾지 못했습니다.")
        return None

# ------------------------------------------------------------------
# 🎯 [신규 함수]: 정규표현식으로 마커 포함 구문 100% 탐지
# ------------------------------------------------------------------
def _find_policies_by_marker_regex(context: str) -> List[Dict[str, str]]:
    """RAG 컨텍스트 내에서 <신설 YYYY.M.D.> 마커를 포함한 정책 구문을 정규표현식으로 추출 및 정규화."""
    
    # 🚨 [핵심 수정 1]: RAG 컨텍스트에서 출처(Source) 정보와 관련된 모든 문자열을 미리 제거합니다.
    context_clean = re.sub(r'\[출처:.*?\.pdf\]', '', context, flags=re.DOTALL)
    context_clean = re.sub(r'---\n', '', context_clean, flags=re.DOTALL)
    
    # 🎯 [수정된 정규식]: 조항 기호로 시작하고 마커로 끝나는 구문을 정확히 탐지합니다.
    # [\s\S]*?는 개행 문자를 포함하여 비탐욕적(non-greedy)으로 마커 직전까지의 모든 텍스트를 잡습니다.
    regex = r"([\n\s]*([가-힣\d]+\.|\([가-힣\d]+\))[\s\S]*?)\< *(신설|개정)\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\.\s*>"
    
    matches = re.findall(regex, context_clean, re.DOTALL) 
    
    extracted_changes = []
    
    for full_text, start_marker, change_type, year, month, day in matches:
        # 정책 내용: 마커 직전의 텍스트와 마커를 포함
        policy_text_with_marker = full_text.strip()
        
        # 🚨 [핵심 수정 2]: 띄어쓰기가 없는 한글/영어/숫자 사이에 공백을 삽입하여 텍스트를 정규화합니다.
        normalized_text = re.sub(r'([가-힣a-zA-Z\d])([가-힣a-zA-Z\d])', r'\1 \2', policy_text_with_marker).strip()
        # 다중 공백을 단일 공백으로 치환
        normalized_text = re.sub(r'\s{2,}', ' ', normalized_text)
        
        try:
            effective_date = datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            effective_date = "N/A"

        # 마커의 날짜가 2025-03-05 이후인지 확인 (파일 날짜 기준)
        if effective_date >= "2025-03-05": 
            extracted_changes.append({
                "effective_date": effective_date,
                "policy_text": normalized_text
            })

    return extracted_changes


# ------------------------------------------------------------------
# 🎯 [수정된 내부 로직 2]: LLM을 통해 정책 분석 보고서 생성 (구조화된 텍스트 인풋)
# ------------------------------------------------------------------
def _generate_final_report_from_structured_data(report_month_str: str, structured_changes: List[Dict[str, str]]) -> Dict[str, Any]:
    """Python이 찾은 정책 변동 리스트를 LLM에게 넘겨 최종 분석 보고서 텍스트를 생성합니다."""
    
    report_month = datetime.strptime(report_month_str, "%Y-%m-%d").date()
    report_month_str_kr = report_month.strftime('%Y년 %m월')
    
    # LLM 인풋 텍스트 포맷팅
    analysis_input = "\n---\n".join([f"[{c['effective_date']}] {c['policy_text']}" for c in structured_changes])
    
    # 가장 빠른 시행일자를 찾아 분석 보고서 제목에 사용
    earliest_date = structured_changes[0]['effective_date'] if structured_changes else "2025-03-05"

    # 🎯 [프롬프트 변경]: 단일 간결한 분석 요약만 요청하도록 변경
    prompt = f"""
    [System] 당신은 전문 금융 분석가입니다. 아래는 Python을 통해 추출된, 2025년 3월 5일 이후 시행될 정책 변동 사항의 핵심 조항 목록입니다.
    
    이 목록을 기반으로 고객에게 전달할 **간결한 단일 단락 분석 보고서**를 한국어로 작성하십시오.
    
    **보고서 형식:**
    1. 반드시 '📌 [시행일: {earliest_date}]'로 시작하십시오.
    2. 보고서는 헤더, 푸터, 제목 없이 **하나의 간결한 단락**으로 구성하십시오.
    3. 변동 사항의 핵심 내용과 고객에게 미치는 영향을 포함하여 5줄 이내로 요약하십시오.
    4. **정책 변동 사항의 목록** 외에 LTV/DSR 같은 **일반적인 배경 정보**는 포함하지 마십시오.
    
    [추출된 정책 변동 사항]
    {analysis_input}
    
    [간결한 최종 분석 보고서]
    """
    
    payload = {"model": QWEN_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.5}}
    
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=180) 
        final_analysis_report = response.json()['response'].strip()
        
        # 🚨 [Guardrail 필터]: LLM이 추가한 헤더, 섹션, 구분자 등을 강제로 제거하고 단일 문장으로 만듭니다.
        
        # 1. 모든 Markdown Headers 및 구분자 제거
        cleaned_report = re.sub(r'(#+|--+|=+)\s*.*?\n', ' ', final_analysis_report, flags=re.DOTALL)
        # 2. 다중 공백 단일화 및 앞뒤 공백 제거
        cleaned_report = re.sub(r'\s{2,}', ' ', cleaned_report).strip()
        
        # 3. '📌 [시행일: YYYY-MM-DD]' 접두사 강제 적용 및 재정렬
        earliest_date = structured_changes[0]['effective_date'] if structured_changes else "2025-03-05"
        
        # '📌 [시행일: 2025-03-05]' 문자열 자체를 제외한 나머지 내용만 추출
        content_only = re.sub(r'^📌\s*\[시행일:\s*[\d-]*\s*\]\s*', '', cleaned_report).strip()
        
        final_analysis_report = f"📌 [시행일: {earliest_date}] {content_only}"
        
        # LLM이 너무 길게 출력했다면 자르거나, 단일 단락으로 강제 변환
        final_analysis_report = ' '.join(final_analysis_report.split()) # 모든 줄바꿈을 공백으로 바꾸고 단일 단락으로 강제 변환
        
        return {
            "analysis_report": final_analysis_report, 
            "error": None,
        }

    except Exception as e:
        logger.error(f"최종 정책 분석 LLM 오류: {e}", exc_info=True)
        return {
            "analysis_report": "최종 정책 분석 보고서 생성 중 시스템 오류 발생", 
            "error": str(e)
        }

# ==============================================================================
# 독립 Tool 1: 소비 데이터 분석 및 군집 생성 (복구)
# ==============================================================================
@router.post(
    "/analyze_user_spending",
    summary="월별 소비 데이터 비교 분석 및 군집 생성",
    operation_id="analyze_user_spending_tool", 
    description="두 달치 소비 데이터(DataFrame Records)를 받아 총 지출, Top 3 카테고리를 비교 분석하고, 군집 별명과 조언을 LLM을 통해 생성합니다.",
    response_model=dict,
)
async def analyze_user_spending(
    consume_records: List[Dict[str, Any]] = Body(..., embed=True),
    member_data: Dict[str, Any] = Body(..., embed=False),
    ollama_model: Optional[str] = Body(QWEN_MODEL, embed=False)
) -> dict:
    """소비 데이터를 기반으로 군집을 분석하고, LLM을 통해 조언을 생성합니다."""
    if not consume_records or len(consume_records) < 2:
        return {"tool_name": "analyze_user_spending_tool", "success": False, "error": "비교 분석을 위한 최소 2개월 데이터 부족"}
    
    try:
        df_consume = pd.DataFrame(consume_records)
        df_consume['spend_month'] = pd.to_datetime(df_consume['spend_month'])
        df_consume = df_consume.sort_values(by='spend_month', ascending=False)
        
        feb_data = df_consume.iloc[0] 
        jan_data = df_consume.iloc[1]

        total_spend_feb = feb_data.get('total_spend', 0) or 0
        total_spend_jan = jan_data.get('total_spend', 0) or 0
        diff = total_spend_feb - total_spend_jan
        change_rate = (diff / total_spend_jan) * 100 if total_spend_jan else 0

        cat1_cols = [col for col in feb_data.index if col.startswith('CAT1_')]
        feb_cats = df_consume.iloc[0][cat1_cols].sort_values(ascending=False).head(3) # 최신 데이터 사용
        
        # 🎯 [수정] 아웃풋 필드명: consume_analysis_summary에 맞춤
        consume_analysis_summary = {
            'latest_total_spend': f"{total_spend_feb:,}",
            'total_change_diff': f"{diff:+,}",
            'top_3_categories': [col.replace('CAT1_', '') for col in feb_cats.index],
            'member_info': member_data
        }

        nickname = f"레저/여행 집중형 고객" # LLM이 변경할 수 있지만, 기본값 설정
        prompt = f"""
        [System] 당신은 고객의 소비 분석가입니다. 아래 분석 결과를 바탕으로 고객에게 전달할 4줄의 **간결하고 정중한** 소비 분석 보고서와 저축/투자 조언을 한국어로 작성하십시오.
        [분석 결과]
        총 지출: {consume_analysis_summary['latest_total_spend']}원, 변화: {consume_analysis_summary['total_change_diff']}원. 
        주 소비 영역: {', '.join(consume_analysis_summary['top_3_categories'])}. 
        고객 정보: {member_data}
        [보고서 형식]
        1. 군집 별명 언급: {nickname}
        2. 지출 변화 해석 및 주요 카테고리 설명
        3. 연봉/부채 등을 고려한 저축/투자 조언 한 줄 포함 (예: "증가한 지출을 감안하여..." 또는 "안정적인 연봉을 바탕으로...")
        """
        
        payload = {"model": QWEN_MODEL, "prompt": prompt, "stream": False}
        
        response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=180) 
        consume_report = response.json()['response'].strip()
        
        # 🎯 [수정] 아웃풋 필드명: consume_report, consume_analysis_summary
        return {
            "tool_name": "analyze_user_spending_tool", 
            "success": True, 
            "consume_report": consume_report,
            "cluster_nickname": nickname,
            "consume_analysis_summary": consume_analysis_summary
        }

    except Exception as e:
        logger.error(f"소비 분석 오류: {e}")
        return {"tool_name": "analyze_user_spending_tool", "success": False, "error": str(e)}

# ==============================================================================
# 독립 Tool 2: 최종 3줄 요약 LLM Tool (복구)
# ==============================================================================
@router.post(
    "/generate_final_summary",
    summary="최종 보고서 3줄 요약 생성",
    operation_id="generate_final_summary_llm", 
    description="통합 보고서 본문을 받아 핵심 내용을 3줄로 간결하게 요약합니다.",
    response_model=dict,
)
async def api_generate_final_summary(report_content: str = Body(..., embed=True)) -> dict:
    """Agent가 보고서 본문을 전송하면, LLM을 통해 3줄 핵심 요약본을 생성합니다."""
    
    # 🎯 [수정] 구분자 무시 지침 포함
    prompt_template = f"""
    [System] 당신은 전문 분석가입니다. 아래 통합 보고서 내용을 읽고, **가장 핵심적인 3가지 사항**만 뽑아 간결하게 **3줄**로 요약하십시오. 보고서 본문 외의 설명이나 제목, 또는 구분자(---SECTION_END---)와 같은 **불필요한 기호는 모두 무시**하십시오.
    
    [통합 보고서 내용]
    {report_content}
    
    [3줄 요약]
    """
    
    payload = {"model": QWEN_MODEL, "prompt": prompt_template, "stream": False, "options": {"temperature": 0.3}}
    
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=180) 
        final_summary = response.json()['response'].strip()
        lines = [line.strip() for line in final_summary.split('\n') if line.strip()]
        threelines_summary = "\n".join(lines[:3]) # 🎯 [수정] 아웃풋 필드명에 맞춤
        
        return {"tool_name": "generate_final_summary_llm", "success": True, "threelines_summary": threelines_summary}
    except requests.exceptions.RequestException as e:
        error_msg = f"Ollama 통신 오류: {e}"
        return {"tool_name": "generate_final_summary_llm", "success": False, "error": error_msg, "threelines_summary": "3줄 요약 생성 실패"}


# # ------------------------------------------------------------------
# 🎯 툴 3-D: 정책 변동 자동 비교 및 보고서 생성 툴 (핵심)
# ------------------------------------------------------------------
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
    """매월 정기 보고서 생성을 위해 정책 변동 사항을 자동으로 비교하고 보고서를 생성합니다."""
    
    # 🎯 [수정 1] RAG 검색 대상을 동적으로 찾습니다.
    POLICY_DOCUMENT_DIR = '../data/policy_documents' # 경로 수정 반영
    LATEST_POLICY_SOURCE = _find_latest_policy_file(POLICY_DOCUMENT_DIR) # _find_latest_policy_file 함수는 외부 정의됨
    
    if not LATEST_POLICY_SOURCE:
        analysis_report = "정책 문서 디렉토리에서 유효한 최신 정책 파일을 찾을 수 없습니다."
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": False, 
            "analysis_report": analysis_report, 
            "policy_changes": [],
            "error": "최신 정책 파일 검색 실패",
        }
        
    REQUIRED_SOURCES = [LATEST_POLICY_SOURCE] 
    
    # 1. 📅 날짜 체크 및 초기 설정 (보고서 유효성 체크 및 단일 보고 주기 체크)
    try:
        report_month = datetime.strptime(report_month_str, "%Y-%m-%d").date()
        
        # 최신 정책 파일 날짜 추출
        file_name = Path(LATEST_POLICY_SOURCE).name 
        latest_policy_date_str = file_name.split('_')[0] # 'YYYYMMDD' 추출
        latest_policy_date = datetime.strptime(latest_policy_date_str, "%Y%m%d").date()
        
        # 🎯 [수정 2] 최소 필터 날짜를 최신 정책 파일 날짜와 동일하게 설정
        MINIMUM_FILTER_DATE_DT = latest_policy_date 
        
        # ----------------------------------------------------------------------
        # 🎯 [핵심 추가] 단일 보고 주기 확인 로직: 정책 변동은 다음 달 보고서에만 반영
        # ----------------------------------------------------------------------
        
        # 1. 최신 정책 문서 날짜 이후의 '다음 달 1일'을 계산합니다. (Target Report Month)
        policy_year = latest_policy_date.year
        policy_month = latest_policy_date.month
        
        # 다음 달 계산 (12월 -> 다음 해 1월로 정확히 넘어감)
        next_month = (policy_month % 12) + 1
        next_year = policy_year + (1 if policy_month == 12 else 0)
        target_report_month_start = date(next_year, next_month, 1)
        
        # 2. 현재 요청된 보고서 월의 시작일을 계산합니다.
        report_month_start = report_month.replace(day=1)
        
        # 3. Target Month가 아니라면, 변동 없음 처리 (이미 보고되었거나, 아직 정책 시행 전)
        if report_month_start != target_report_month_start:
            
            # A. 보고서 월이 정책 시행일보다 이전인 경우 (아직 정책 시행 전이거나 파일 날짜 이전)
            if report_month < latest_policy_date:
                policy_analysis_report = f"{report_month.strftime('%Y년 %m월')} 보고서 기준, **최신 정책 문서({latest_policy_date.strftime('%Y년 %m월 %d일')})**가 아직 유효하지 않아 정책 변동 사항은 없습니다."
            
            # B. 보고서 월이 정책 시행 월보다 이후인 경우 (이미 지난달에 보고 완료)
            else: 
                policy_analysis_report = f"최신 정책 문서({latest_policy_date.strftime('%Y년 %m월 %d일')})의 변동 사항은 이미 {target_report_month_start.strftime('%Y년 %m월')} 보고서에 반영되었으며, 현재({report_month_start.strftime('%Y년 %m월')}) 기준으로 새로운 정책 변동 사항은 확인되지 않았습니다."
            
            # 결과 반환
            return {
                "tool_name": "check_and_report_policy_changes_tool", 
                "success": True, 
                "analysis_report": policy_analysis_report,
                "policy_changes": [],
                "error": None,
            }
        
    except ValueError:
        analysis_report = "유효하지 않은 보고서 월 형식입니다."
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": False, 
            "analysis_report": analysis_report, # KEY CHANGE
            "policy_changes": [],
            "error": "보고서 월 형식 오류",
        }
    
    # ----------------------------------------------------------------------
    # 4. [통과] 보고서 월이 Target Month와 일치하므로, 변동사항 추출 시작
    # ----------------------------------------------------------------------
    
    # 2. ⚙️ 정책 섹션별로 RAG 검색 실행 (최신 파일만 대상)
    full_context_list = []
    
    # 🎯 [수정] 정책 누락 방지를 위해 RAG 검색 깊이 K_SEARCH를 7로 유지합니다.
    K_SEARCH = 7 
    
    for section_query in POLICY_SECTIONS_TO_CHECK:
        rag_context = _rag_similarity_search(
            query=section_query, 
            k=K_SEARCH, 
            required_sources=REQUIRED_SOURCES 
        )

        if "🚨 RAG 검색 실패" not in rag_context:
            full_context_list.append(rag_context)
        else:
            # RAG 검색 자체의 시스템 오류는 500이 아닌 툴 에러로 처리
             return {
                "tool_name": "check_and_report_policy_changes_tool", 
                "success": False, 
                "analysis_report": "정책 변동 분석 시스템 오류: RAG 검색 실패", # KEY CHANGE
                "policy_changes": [],
                "error": rag_context,
            }

    combined_context = "\n---\n".join(full_context_list)
    
    # 3. 📝 [핵심 변경] 정규표현식을 이용해 RAG 컨텍스트에서 마커 포함 구문 추출
    structured_changes_raw = _find_policies_by_marker_regex(combined_context)
    
    # 4. 🎯 [최종 파이썬 필터링] 최신 정책 파일 날짜 이전 항목 제거
    filtered_changes = []
    
    for change in structured_changes_raw:
        effective_date_str = change.get("effective_date")
        if not effective_date_str or effective_date_str == "N/A":
            continue
            
        try:
            effective_date = datetime.strptime(effective_date_str, "%Y-%m-%d").date()
            
            # [최종 필터링] 시행일이 MINIMUM_FILTER_DATE_DT (최신 정책 파일 날짜)와 같거나 이후인 경우만 포함
            if effective_date >= MINIMUM_FILTER_DATE_DT:
                filtered_changes.append(change)
                
        except ValueError:
            # 날짜 형식 오류가 발생한 항목은 제외
            continue
            
    structured_changes = filtered_changes

    # 5. 🧩 추출 결과 처리 (변동 사항이 없는 경우에도 Target Month라면, 정책 파일 자체에 변동 사항이 없는 경우)
    if not structured_changes:
        # 정책 파일은 최신인데, 그 안에 마커로 표시된 신설/개정 사항이 하나도 없는 경우
        policy_analysis_report = f"{report_month.strftime('%Y년 %m월')} 보고서 기준, 최신 정책 문서({latest_policy_date.strftime('%Y년 %m월 %d일')})에 **신설 또는 개정된 정책 변동 사항은 확인되지 않았습니다.**"
        return {
            "tool_name": "check_and_report_policy_changes_tool", 
            "success": True, 
            "analysis_report": policy_analysis_report, # KEY CHANGE
            "policy_changes": [],
            "error": None,
        }

    # 6. 📝 LLM에게 분석 요청 및 최종 보고서 생성
    report_result = _generate_final_report_from_structured_data(report_month_str, structured_changes)
    
    final_analysis_report = report_result['analysis_report']
    
    # 7. 🎯 최종 아웃풋 구성
    return {
        "tool_name": "check_and_report_policy_changes_tool", 
        "success": report_result['error'] is None, 
        "analysis_report": final_analysis_report, # KEY CHANGE
        "policy_changes": structured_changes, # Python이 찾은 정확한 리스트를 반환
        "error": report_result['error']
    }


# ==============================================================================
# 독립 Tool 1: 소비 데이터 분석 및 군집 생성 (복구)
# ==============================================================================
@router.post(
    "/analyze_user_spending",
    summary="월별 소비 데이터 비교 분석 및 군집 생성",
    operation_id="analyze_user_spending_tool", 
    description="두 달치 소비 데이터(DataFrame Records)를 받아 총 지출, Top 3 카테고리를 비교 분석하고, 군집 별명과 조언을 LLM을 통해 생성합니다.",
    response_model=dict,
)
async def analyze_user_spending(
    consume_records: List[Dict[str, Any]] = Body(..., embed=True),
    member_data: Dict[str, Any] = Body(..., embed=False),
    ollama_model: Optional[str] = Body(QWEN_MODEL, embed=False)
) -> dict:
    """소비 데이터를 기반으로 군집을 분석하고, LLM을 통해 조언을 생성합니다."""
    if not consume_records or len(consume_records) < 2:
        return {"tool_name": "analyze_user_spending_tool", "success": False, "error": "비교 분석을 위한 최소 2개월 데이터 부족"}
    
    try:
        df_consume = pd.DataFrame(consume_records)
        df_consume['spend_month'] = pd.to_datetime(df_consume['spend_month'])
        df_consume = df_consume.sort_values(by='spend_month', ascending=False)
        
        feb_data = df_consume.iloc[0] 
        jan_data = df_consume.iloc[1]

        total_spend_feb = feb_data.get('total_spend', 0) or 0
        total_spend_jan = jan_data.get('total_spend', 0) or 0
        diff = total_spend_feb - total_spend_jan
        change_rate = (diff / total_spend_jan) * 100 if total_spend_jan else 0

        cat1_cols = [col for col in feb_data.index if col.startswith('CAT1_')]
        feb_cats = df_consume.iloc[0][cat1_cols].sort_values(ascending=False).head(3) # 최신 데이터 사용
        
        # 🎯 [수정] 아웃풋 필드명: consume_analysis_summary에 맞춤
        consume_analysis_summary = {
            'latest_total_spend': f"{total_spend_feb:,}",
            'total_change_diff': f"{diff:+,}",
            'top_3_categories': [col.replace('CAT1_', '') for col in feb_cats.index],
            'member_info': member_data
        }

        nickname = f"레저/여행 집중형 고객" # LLM이 변경할 수 있지만, 기본값 설정
        prompt = f"""
        [System] 당신은 고객의 소비 분석가입니다. 아래 분석 결과를 바탕으로 고객에게 전달할 4줄의 **간결하고 정중한** 소비 분석 보고서와 저축/투자 조언을 한국어로 작성하십시오.
        [분석 결과]
        총 지출: {consume_analysis_summary['latest_total_spend']}원, 변화: {consume_analysis_summary['total_change_diff']}원. 
        주 소비 영역: {', '.join(consume_analysis_summary['top_3_categories'])}. 
        고객 정보: {member_data}
        [보고서 형식]
        1. 군집 별명 언급: {nickname}
        2. 지출 변화 해석 및 주요 카테고리 설명
        3. 연봉/부채 등을 고려한 저축/투자 조언 한 줄 포함 (예: "증가한 지출을 감안하여..." 또는 "안정적인 연봉을 바탕으로...")
        """
        
        payload = {"model": QWEN_MODEL, "prompt": prompt, "stream": False}
        
        response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=180) 
        consume_report = response.json()['response'].strip()
        
        # 🎯 [수정] 아웃풋 필드명: consume_report, consume_analysis_summary
        return {
            "tool_name": "analyze_user_spending_tool", 
            "success": True, 
            "consume_report": consume_report,
            "cluster_nickname": nickname,
            "consume_analysis_summary": consume_analysis_summary
        }

    except Exception as e:
        logger.error(f"소비 분석 오류: {e}")
        return {"tool_name": "analyze_user_spending_tool", "success": False, "error": str(e)}

# ==============================================================================
# 독립 Tool 2: 최종 3줄 요약 LLM Tool (복구)
# ==============================================================================
@router.post(
    "/generate_final_summary",
    summary="최종 보고서 3줄 요약 생성",
    operation_id="generate_final_summary_llm", 
    description="통합 보고서 본문을 받아 핵심 내용을 3줄로 간결하게 요약합니다.",
    response_model=dict,
)
async def api_generate_final_summary(report_content: str = Body(..., embed=True)) -> dict:
    """Agent가 보고서 본문을 전송하면, LLM을 통해 3줄 핵심 요약본을 생성합니다."""
    
    # 🎯 [수정] 구분자 무시 지침 포함
    prompt_template = f"""
    [System] 당신은 전문 분석가입니다. 아래 통합 보고서 내용을 읽고, **가장 핵심적인 3가지 사항**만 뽑아 간결하게 **3줄**로 요약하십시오. 보고서 본문 외의 설명이나 제목, 또는 구분자(---SECTION_END---)와 같은 **불필요한 기호는 모두 무시**하십시오.
    
    [통합 보고서 내용]
    {report_content}
    
    [3줄 요약]
    """
    
    payload = {"model": QWEN_MODEL, "prompt": prompt_template, "stream": False, "options": {"temperature": 0.3}}
    
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=180) 
        final_summary = response.json()['response'].strip()
        lines = [line.strip() for line in final_summary.split('\n') if line.strip()]
        threelines_summary = "\n".join(lines[:3]) # 🎯 [수정] 아웃풋 필드명에 맞춤
        
        return {"tool_name": "generate_final_summary_llm", "success": True, "threelines_summary": threelines_summary}
    except requests.exceptions.RequestException as e:
        error_msg = f"Ollama 통신 오류: {e}"
        return {"tool_name": "generate_final_summary_llm", "success": False, "error": error_msg, "threelines_summary": "3줄 요약 생성 실패"}


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
    """보유 상품 목록을 기반으로 손익 분석 및 조언을 생성합니다."""
    
    if not products:
        return {
            "tool_name": "analyze_investment_profit_tool", 
            "success": True, 
            "error": None,
            "profit_analysis_report": "현재 보유 중인 투자 상품이 없어 분석을 건너킵니다."
        }

    # 1. 📊 상품 데이터 처리 (분석)
    total_principal = 0
    total_valuation = 0
    
    # 🎯 실제 DB 스키마에 따라 total_principal, current_valuation 필드를 가정
    for p in products:
        principal = p.get('total_principal', 0)
        valuation = p.get('current_valuation', 0)
        total_principal += principal
        total_valuation += valuation 

    net_profit = total_valuation - total_principal
    profit_rate = (net_profit / total_principal) * 100 if total_principal else 0
    
    # 2. 💬 [LLM] 분석 요청 (진척도 및 조언 생성)
    data_summary = f"""
    [투자 분석 요약]
    - 총 투자 원금: {total_principal:,}원
    - 현재 평가액: {total_valuation:,}원
    - 순손익: {net_profit:+,}원
    - 수익률: {profit_rate:.2f}%
    - 보유 상품 수: {len(products)}개
    """
    
    prompt = f"""
    [System] 당신은 전문 투자 조언가입니다. 아래 투자 분석 요약을 보고, 고객에게 현재의 투자 진척도(수익률)에 대해 평가하고 **다음 단계의 투자 전략**에 대한 조언을 5줄 이내로 간결하고 정중하게 한국어로 작성하십시오. (예: "안정적인 수익률이지만, 목표를 달성하려면 분산 투자를 고려해야 합니다.")
    
    {data_summary}
    
    [투자 진척도 평가 및 조언]
    """
    
    payload = {"model": QWEN_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.5}}
    
    try:
        response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=180) 
        profit_analysis_report = response.json()['response'].strip()
        
        return {
            "tool_name": "analyze_investment_profit_tool", 
            "success": True, 
            "profit_analysis_report": profit_analysis_report,
            "net_profit": net_profit,
            "profit_rate": profit_rate,
            "error": None
        }

    except Exception as e:
        logger.error(f"투자 상품 분석 오류: {e}")
        return {
            "tool_name": "analyze_investment_profit_tool", 
            "success": False, 
            "error": f"투자 상품 분석 시스템 오류: {e}"
        }

# ------------------------------------------------------------------
# 🎯 [신규] Tool 5: 사용자 프로필 변동 분석 및 보고서 생성 (추가됨)
# ------------------------------------------------------------------
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
    """사용자의 연봉, 부채, 신용 점수 변동 사항을 분석하고 보고서를 생성합니다."""
    
    # 1. 📊 데이터 비교 및 요약
    change_raw_changes = []
    
    # [연봉 비교]
    current_salary = current_data.get('annual_salary', 0) or 0
    previous_salary = previous_data.get('annual_salary', 0) or 0
    salary_diff = current_salary - previous_salary
    if salary_diff != 0:
        change_raw_changes.append(f"연봉 변동: {previous_salary:,}원 → {current_salary:,}원 ({salary_diff:+,}원)")
    
    # [부채 비교]
    current_debt = current_data.get('total_debt', 0) or 0
    previous_debt = previous_data.get('total_debt', 0) or 0
    debt_diff = current_debt - previous_debt
    if debt_diff != 0:
        change_raw_changes.append(f"총 부채 변동: {previous_debt:,}원 → {current_debt:,}원 ({debt_diff:+,}원)")

    # [신용 점수 비교]
    current_credit = current_data.get('credit_score', 0) or 0
    previous_credit = previous_data.get('credit_score', 0) or 0
    credit_diff = current_credit - previous_credit
    if credit_diff != 0:
        change_raw_changes.append(f"신용 점수 변동: {previous_credit}점 → {current_credit}점 ({credit_diff:+,}점)")
    
    analysis_summary = "\n".join(change_raw_changes) if change_raw_changes else "직전 보고서 대비 주요 개인 금융 지표(연봉, 부채, 신용 점수)의 변동 사항은 없습니다."
    
    # 2. 💬 LLM 프롬프트 생성 (변동 사항 분석 요청)
    if not change_raw_changes:
        change_analysis_report = "직전 보고서 대비 고객님의 주요 개인 지표(연봉, 부채, 신용 점수)에 큰 변동 사항이 없어 특이 보고는 생략합니다."
        success = True
    else:
        prompt = f"""
        [System] 당신은 고객의 개인 금융 지표(연봉, 부채, 신용 점수) 변동 분석가입니다. 아래 비교 결과를 바탕으로 고객에게 전달할 4줄의 **간결하고 정중한** 변동 분석 보고서와 개인 재정 상황에 맞는 조언을 한국어로 작성하십시오.
        
        [지표 변동 결과]
        {analysis_summary}
        
        [보고서 형식]
        1. 신용 점수 변화를 포함하여 지표 변동의 핵심 요약
        2. 부채/연봉 변화에 따른 재정 건전성 평가
        3. 변동된 상황을 바탕으로 다음 단계에서 고려해야 할 재정 조언
        """
        
        payload = {"model": QWEN_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.5}}
        
        try:
            response = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=180) 
            change_analysis_report = response.json()['response'].strip()
            success = True
        except Exception as e:
            logger.error(f"사용자 변동 분석 LLM 오류: {e}")
            change_analysis_report = "사용자 변동 분석 보고서 생성 중 오류 발생"
            success = False

    # 🎯 [수정 완료] 아웃풋 필드명을 요청하신대로 변경
    return {
        "tool_name": "analyze_user_profile_changes_tool", 
        "success": success, 
        "change_analysis_report": change_analysis_report,
        "change_raw_changes": change_raw_changes
    }