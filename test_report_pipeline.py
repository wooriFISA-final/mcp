import asyncio
import json
import logging
from typing import Dict, Any, List
from datetime import datetime
from dateutil.relativedelta import relativedelta

from server.api.resources.report_db_tools import api_get_member_details, api_fetch_user_consume_data, api_fetch_recent_report_summary, api_fetch_user_products, api_save_monthly_report
from server.api.tools.report_agent_tools import (
    analyze_user_spending, 
    analyze_user_profile_changes, 
    api_analyze_investment_profit, 
    api_check_policy_changes, 
    api_generate_final_summary
)

# ----------------------------------------------------
# 🎯 설정 변수
# ----------------------------------------------------
USER_ID = 1
MEMBER_ID = USER_ID 
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ----------------------------------------------------
# ⚙️ 도우미 함수: 월 목록 생성
# ----------------------------------------------------
def get_report_months(start_year: int, start_month: int, end_year: int, end_month: int) -> List[str]:
    """지정된 기간 동안의 'YYYY-MM-01' 형식 보고서 날짜 목록을 생성합니다."""
    dates = []
    
    current_date = datetime(start_year, start_month, 1)
    end_date = datetime(end_year, end_month, 1)
    
    while current_date <= end_date:
        dates.append(current_date.strftime("%Y-%m-%d"))
        current_date += relativedelta(months=1)
            
    return dates

# ----------------------------------------------------
# 🚀 월별 보고서 작성을 위한 핵심 오케스트레이션 함수 (실제 Agent 호출)
# ----------------------------------------------------
async def run_report_pipeline_monthly(report_date_str: str, consume_dates: List[str]):
    """
    단일 월에 대한 보고서 생성 파이프라인을 실행합니다.
    """
    
    target_month_ym = consume_dates[0] 
    
    print(f"\n========================================================")
    print(f"--- 📊 {target_month_ym} 월 보고서 작성 시작 (저장일: {report_date_str}) ---")
    
    # 🚨 모든 핵심 metadata 키를 초기화하여 NameError 방지
    metadata = {
        'change_analysis_report': "변동 분석 보고서 없음",
        'change_raw_changes': [],
        'consume_report': "소비 분석 보고서 없음",
        'cluster_nickname': "분석 불가",
        'consume_analysis_summary': {},
        'spend_chart_json': json.dumps([]),
        'profit_analysis_report': "투자 분석 보고서 없음",
        'net_profit': 0,
        'profit_rate': 0.0,
        'policy_analysis_report': "정책 보고서 없음",
        'policy_changes': [],
        'threelines_summary': "3줄 요약 생성 실패"
    }
    
    # 1. DB: 현재 사용자 상세 금융/신용 정보 조회 (실제 DB 호출)
    print(f"  [Step 1/9] 👤 현재 사용자 정보 조회...")
    current_member_data_response = await api_get_member_details(user_id=USER_ID)
    current_member_data = current_member_data_response.get('data', {})
    
    if not current_member_data_response.get('success'):
        print(f"  🚨 오류: 사용자 상세 정보 조회 실패: {current_member_data_response.get('error')}")
        return

    # 2. DB: 직전 월 레포트 요약 조회 (실제 DB 호출)
    previous_report_date = datetime.strptime(report_date_str, "%Y-%m-%d") - relativedelta(months=1)
    previous_report_date_str = previous_report_date.strftime("%Y-%m-%d")

    print(f"  [Step 2/9] 🗓️ 직전 보고서 메타데이터 조회 (비교 대상: {previous_report_date_str})...")
    prev_report_response = await api_fetch_recent_report_summary(
        member_id=MEMBER_ID, 
        report_date_for_comparison=previous_report_date_str
    )
    previous_member_data = prev_report_response.get('data', {})
    
    # 3. Agent Tool: 개인 지수 변동 분석 (실제 LLM 호출)
    print("  [Step 3/9] 📉 개인 지수 변동 분석 (LLM 대기)...")
    change_analysis_response = await analyze_user_profile_changes(
        current_data=current_member_data, 
        previous_data=previous_member_data
    )
    
    # 🚨 [수정 적용]: 응답 딕셔너리에서 안전하게 get()으로 값 추출
    metadata['change_analysis_report'] = change_analysis_response.get('change_analysis_report', metadata['change_analysis_report'])
    metadata['change_raw_changes'] = change_analysis_response.get('change_raw_changes', metadata['change_raw_changes'])
    
    # 4. DB: 소비 데이터 조회 (실제 DB 호출)
    print(f"  [Step 4/9] 🛒 소비 데이터 조회 (기간: {consume_dates})...")
    consume_raw_response = await api_fetch_user_consume_data(user_id=USER_ID, dates=consume_dates)
    consume_records = consume_raw_response.get('data', [])
    
    # 5. Agent Tool: 소비 데이터 분석 (실제 LLM 호출)
    print("  [Step 5/9] 📈 소비 분석 (LLM 대기)...")
    if consume_records:
        spending_analysis_response = await analyze_user_spending(
            consume_records=consume_records, 
            member_data=current_member_data
        )
        # 🚨 [수정 적용]: 응답 딕셔너리에서 안전하게 get()으로 값 추출
        metadata['consume_report'] = spending_analysis_response.get('consume_report', metadata['consume_report'])
        metadata['cluster_nickname'] = spending_analysis_response.get('cluster_nickname', metadata['cluster_nickname'])
        metadata['consume_analysis_summary'] = spending_analysis_response.get('consume_analysis_summary', metadata['consume_analysis_summary'])
        metadata['spend_chart_json'] = spending_analysis_response.get('spend_chart_json', metadata['spend_chart_json'])
    
    # 6. DB & Agent Tool: 투자 상품 분석 (실제 LLM 호출)
    print("  [Step 6/9] 💰 투자 상품 조회 및 분석 (LLM 대기)...")
    products_response = await api_fetch_user_products(user_id=USER_ID)
    products = products_response.get('data', [])
    
    investment_analysis_response = await api_analyze_investment_profit(products=products)
    # 🚨 [수정 적용]: 응답 딕셔너리에서 안전하게 get()으로 값 추출
    metadata['profit_analysis_report'] = investment_analysis_response.get('profit_analysis_report', metadata['profit_analysis_report'])
    metadata['net_profit'] = investment_analysis_response.get('net_profit', metadata['net_profit'])
    metadata['profit_rate'] = investment_analysis_response.get('profit_rate', metadata['profit_rate'])
    
    # 7. Agent Tool: 정책 변동 RAG 분석 (실제 LLM 호출)
    print("  [Step 7/9] 📜 정책 변동 RAG 분석 (LLM 대기)...")
    policy_response = await api_check_policy_changes(report_month_str=report_date_str)
    # 🚨 [수정 적용]: 응답 딕셔너리에서 안전하게 get()으로 값 추출
    metadata['policy_analysis_report'] = policy_response.get('analysis_report', metadata['policy_analysis_report'])
    metadata['policy_changes'] = policy_response.get('policy_changes', metadata['policy_changes'])

    # --- 최종 보고서 통합 및 요약 ---
    
    # 임시로 통합 보고서 본문 생성
    full_report_content = "--- SECTION_END ---\n" 
    full_report_content += "## 👤 개인 재정 지표 변동\n" + metadata['change_analysis_report'] + "\n"
    full_report_content += "## 📈 소비 습관 분석\n" + metadata['consume_report'] + "\n"
    full_report_content += "## 💰 투자 진척도\n" + metadata['profit_analysis_report'] + "\n"
    full_report_content += "## 📜 금융 정책 브리핑\n" + metadata['policy_analysis_report'] + "\n"
    
    # 8. Agent Tool: 3줄 요약 생성 (실제 LLM 호출)
    print("  [Step 8/9] 📄 최종 3줄 요약 생성 (LLM 대기)...")
    summary_response = await api_generate_final_summary(report_content=full_report_content)
    threelines_summary = summary_response.get('threelines_summary', metadata['threelines_summary'])
    
    metadata['threelines_summary'] = threelines_summary
    
    # 9. DB: 최종 저장 (실제 DB 호출)
    print(f"  [Step 9/9] 💾 최종 {target_month_ym} 월 보고서 DB 저장 (저장일: {report_date_str})...")
    save_response = await api_save_monthly_report(
        member_id=MEMBER_ID, 
        report_date=report_date_str, 
        report_text=threelines_summary,
        metadata=metadata
    )
    
    if save_response.get('success'):
        print(f"--- ✅ {target_month_ym} 월 보고서 생성 및 DB 저장 성공 ---")
    else:
        print(f"--- ❌ {target_month_ym} 최종 DB 저장 실패 ---")
        print(f"  오류: {save_response.get('error')}")
        
    print(f"--- 📝 {target_month_ym} 파이프라인 종료 ---")
    print("========================================================")


# ----------------------------------------------------
# 🏁 메인 실행 로직: 기간 설정 및 반복 실행
# ----------------------------------------------------

async def main_orchestrator():
    # 🚨 실행 기간 설정: 2023년 1월 ~ 2023년 2월 (2개월 테스트)
    START_DATE = (2023, 12) # 2023-01-01 저장일 (2022년 12월 보고서)
    END_DATE = (2025, 9)   # 2023-02-01 저장일 (2023년 1월 보고서)
    
    report_dates_str = get_report_months(START_DATE[0], START_DATE[1], END_DATE[0], END_DATE[1])
    
    print(f"\n========================================================")
    print(f"총 {len(report_dates_str)}개 월 보고서 생성 요청 시작...")
    print("========================================================\n")
    
    for report_date_str in report_dates_str:
        target_report_date = datetime.strptime(report_date_str, "%Y-%m-%d")
        target_consume_date = target_report_date - relativedelta(months=1)
        
        target_consume_ym = target_consume_date.strftime("%Y-%m")
        previous_consume_date = target_consume_date - relativedelta(months=1)
        previous_consume_ym = previous_consume_date.strftime("%Y-%m")
            
        consume_dates = [target_consume_ym, previous_consume_ym]
        
        await run_report_pipeline_monthly(report_date_str, consume_dates)
        
        await asyncio.sleep(1) 


# Python 환경에서 비동기 함수 실행
if __name__ == "__main__":
    try:
        asyncio.run(main_orchestrator())
    except Exception as e:
        print(f"파이프라인 실행 중 치명적인 오류 발생: {type(e).__name__}: {e}")