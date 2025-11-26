import asyncio
import json
from typing import Dict, Any

# DB 툴과 Agent 툴 함수를 임포트 (파일 경로에 따라 수정 필요)
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
# 보고서 저장 날짜: 12월 1일 (12월 보고서)
REPORT_DATE_STR = "2022-12-01" 
# 소비 데이터 조회 기간: 주요 분석 대상월(12월)과 직전월(11월)
CONSUME_DATES = ["2022-12", "2022-11"] 

# ----------------------------------------------------
# 🚀 메인 오케스트레이션 함수 (Agent 역할)
# ----------------------------------------------------
async def run_report_pipeline():
    print(f"--- 📊 {REPORT_DATE_STR} 보고서 작성 시작 (User ID: {USER_ID}) ---")
    
    # 최종 결과 저장 딕셔너리
    report_data = {}
    metadata = {}
    
    # 1. DB: 현재 사용자 상세 금융/신용 정보 조회
    print("\n[Step 1/9] 👤 현재 사용자 정보 조회...")
    current_member_data_response = await api_get_member_details(user_id=USER_ID)
    current_member_data = current_member_data_response.get('data', {})
    
    if not current_member_data_response.get('success'):
        print(f"🚨 오류: 사용자 상세 정보 조회 실패: {current_member_data_response.get('error')}")
        return

    # 2. DB: 직전 월 레포트 요약 조회 (비교 기준)
    print("[Step 2/9] 🗓️ 직전 보고서 메타데이터 조회...")
    prev_report_response = await api_fetch_recent_report_summary(member_id=MEMBER_ID)
    previous_member_data = prev_report_response.get('data', {})
    
    # 3. LLM Tool: 개인 지수 변동 분석
    print("[Step 3/9] 📉 개인 지수 변동 분석 (LLM Tool)...")
    change_analysis_response = await analyze_user_profile_changes(
        current_data=current_member_data, 
        previous_data=previous_member_data
    )
    if change_analysis_response.get('success'):
        metadata['change_analysis_report'] = change_analysis_response['change_analysis_report']
        metadata['change_raw_changes'] = change_analysis_response['change_raw_changes']
    
    # 4. DB: 소비 데이터 조회
    print(f"[Step 4/9] 🛒 소비 데이터 조회 (기간: {CONSUME_DATES})...")
    consume_raw_response = await api_fetch_user_consume_data(user_id=USER_ID, dates=CONSUME_DATES)
    consume_records = consume_raw_response.get('data', [])
    
    # 5. LLM Tool: 소비 데이터 분석
    print("[Step 5/9] 📈 소비 분석 (LLM Tool)...")
    if consume_records:
        spending_analysis_response = await analyze_user_spending(
            consume_records=consume_records, 
            member_data=current_member_data
        )
        if spending_analysis_response.get('success'):
            metadata['consume_report'] = spending_analysis_response['consume_report']
            metadata['cluster_nickname'] = spending_analysis_response['cluster_nickname']
            metadata['consume_analysis_summary'] = spending_analysis_response['consume_analysis_summary']
            
            # 🚨 [수정된 부분]: Agent Tool이 반환한 spend_chart_json 값을 그대로 저장
            metadata['spend_chart_json'] = spending_analysis_response.get('spend_chart_json', json.dumps({}))
    
    # 6. DB & LLM Tool: 투자 상품 분석
    print("[Step 6/9] 💰 투자 상품 조회 및 분석 (LLM Tool)...")
    products_response = await api_fetch_user_products(user_id=USER_ID)
    products = products_response.get('data', [])
    
    investment_analysis_response = await api_analyze_investment_profit(products=products)
    if investment_analysis_response.get('success'):
        metadata['profit_analysis_report'] = investment_analysis_response['profit_analysis_report']
        metadata['net_profit'] = investment_analysis_response.get('net_profit', 0)
        metadata['profit_rate'] = investment_analysis_response.get('profit_rate', 0.0)
    
    # 7. LLM Tool: 정책 변동 RAG 분석
    print("[Step 7/9] 📜 정책 변동 RAG 분석 (LLM Tool)...")
    policy_response = await api_check_policy_changes(report_month_str=REPORT_DATE_STR)
    if policy_response.get('success'):
        metadata['policy_analysis_report'] = policy_response['analysis_report']
        metadata['policy_changes'] = policy_response['policy_changes']

    # --- 최종 보고서 통합 및 요약 ---
    
    # 임시로 통합 보고서 본문 생성 (Agent 역할)
    full_report_content = "--- SECTION_END ---\n" 
    full_report_content += "## 👤 개인 재정 지표 변동\n" + metadata.get('change_analysis_report', "변동 분석 보고서 없음") + "\n"
    full_report_content += "## 📈 소비 습관 분석\n" + metadata.get('consume_report', "소비 보고서 없음") + "\n"
    full_report_content += "## 💰 투자 진척도\n" + metadata.get('profit_analysis_report', "투자 보고서 없음") + "\n"
    full_report_content += "## 📜 금융 정책 브리핑\n" + metadata.get('policy_analysis_report', "정책 보고서 없음") + "\n"
    
    # 8. LLM Tool: 3줄 요약 생성
    print("[Step 8/9] 📄 최종 3줄 요약 생성 (LLM Tool)...")
    summary_response = await api_generate_final_summary(report_content=full_report_content)
    threelines_summary = summary_response.get('threelines_summary', "3줄 요약 생성 실패")
    
    # 최종 보고서 본문 정의: 3줄 요약 + 전체 내용
    final_report_text = f"***[핵심 3줄 요약]***\n{threelines_summary}\n\n{full_report_content}"
    metadata['threelines_summary'] = threelines_summary
    
    # 9. DB: 최종 저장
    print(f"\n[Step 9/9] 💾 최종 보고서 DB 저장 (Report Date: {REPORT_DATE_STR})...")
    save_response = await api_save_monthly_report(
        member_id=MEMBER_ID, 
        report_date=REPORT_DATE_STR, 
        report_text=final_report_text,
        metadata=metadata
    )
    
    if save_response.get('success'):
        print("--- ✅ 보고서 생성 및 DB 저장 성공 ---")
        print(f"저장된 보고서 날짜: {save_response['report_date']}")
    else:
        print(f"--- ❌ 최종 DB 저장 실패 ---")
        print(f"오류: {save_response.get('error')}")
        
    print("\n--- 📝 생성된 최종 보고서 내용 (저장되지 않을 수 있음) ---")
    print(final_report_text)
    print("\n--- 🔑 저장된 메타데이터 ---")
    print(json.dumps(metadata, indent=4, ensure_ascii=False))


# Python 환경에서 비동기 함수 실행
if __name__ == "__main__":
    try:
        asyncio.run(run_report_pipeline())
    except Exception as e:
        print(f"파이프라인 실행 중 치명적인 오류 발생: {e}")