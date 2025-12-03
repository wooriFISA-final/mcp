"""
Agent를 사용하여 과거 리포트를 생성하는 스크립트

2023년 12월부터 2025년 9월까지의 리포트를 Agent(LLM)를 통해 생성합니다.
"""
import asyncio
import aiohttp
from datetime import datetime
from dateutil.relativedelta import relativedelta

# Configuration
AGENT_API_URL = "http://localhost:8080/chat/report"
USER_ID = 1

async def generate_report_via_agent(report_date_str: str, session: aiohttp.ClientSession):
    """
    Agent 서버를 호출하여 리포트 생성
    """
    target_date = datetime.strptime(report_date_str, "%Y-%m-%d")
    target_month_ym = target_date.strftime("%Y-%m")
    
    # Agent에게 전달할 메시지
    message = f"{USER_ID}번 사용자의 {target_month_ym}월 1일 레포트를 작성해줘"
    
    request_data = {
        "message": message,
        "session_id": f"report-gen-{report_date_str}",
        "graph": "report"
    }
    
    print(f"\n>>> 📅 Generating Report for: {target_month_ym} via Agent")
    print(f"    Request: {message}")
    
    try:
        async with session.post(
            AGENT_API_URL,
            json=request_data,
            timeout=aiohttp.ClientTimeout(total=120)  # 2분 타임아웃
        ) as response:
            if response.status == 200:
                result = await response.json()
                print(f"✅ Success: {target_month_ym}")
                print(f"    Response: {result.get('response', '')[:100]}...")
                return True
            else:
                error_text = await response.text()
                print(f"❌ Failed: {target_month_ym} (HTTP {response.status})")
                print(f"    Error: {error_text[:200]}")
                return False
                
    except asyncio.TimeoutError:
        print(f"⏱️ Timeout: {target_month_ym}")
        return False
    except Exception as e:
        print(f"❌ Error: {target_month_ym} - {type(e).__name__}: {e}")
        return False

async def main():
    """
    2023-12부터 2025-09까지 순차적으로 리포트 생성
    """
    start_date = datetime(2025, 10, 1)
    end_date = datetime(2025, 10, 1)
    
    print("="*80)
    print("🚀 Agent 기반 과거 리포트 생성 시작")
    print("="*80)
    print(f"기간: {start_date.strftime('%Y-%m')} ~ {end_date.strftime('%Y-%m')}")
    print(f"Agent URL: {AGENT_API_URL}")
    print("="*80)
    
    current = start_date
    success_count = 0
    fail_count = 0
    
    async with aiohttp.ClientSession() as session:
        while current <= end_date:
            report_date_str = current.strftime("%Y-%m-%d")
            
            success = await generate_report_via_agent(report_date_str, session)
            
            if success:
                success_count += 1
            else:
                fail_count += 1
            
            current += relativedelta(months=1)
            
            # 서버 부하 방지를 위한 짧은 대기
            await asyncio.sleep(2)
    
    print("\n" + "="*80)
    print("📊 생성 완료")
    print("="*80)
    print(f"✅ 성공: {success_count}개")
    print(f"❌ 실패: {fail_count}개")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(main())
