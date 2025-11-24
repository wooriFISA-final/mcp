# mcp_server.py (수정됨)

from fastmcp import FastMCP
from fastapi import FastAPI
from server.api.tools.user_tools import create_user, get_user
from server.api.resources.user_resources import get_user_stats, get_all_users_resource
from server.api.prompts.user_prompts import user_greeting, user_report
from server.routes import data_route
from server.routes import mcp_route

# 🎯 Report Agent Tools 임포트 추가
from server.api.resources import report_db_tools
from server.api.tools import report_agent_tools

from server.api.mcp_admin_routes import create_mcp_admin_router


instructions = (
    "이 MCP 서버는 금액 파싱, 지역 정규화, 퍼센트/비율 파싱, 입력 검증, 예·적금 Top3 필터링, 리스크 레벨별 예상 수익률 Top1만 뽑아주는 순수, 부족 자금(shortage_amount) 계산, 복리 기반 투자 시뮬레이션, DB 조회 기능을 제공합니다."
    # Report Agent Tools에 대한 설명이 필요한 경우 여기에 추가하십시오.
)

# ----------------------------------
# 1. MCP Tools용 앱 (MCP로 변환될 API들)
# ----------------------------------
tools_app = FastAPI()
tools_app.include_router(mcp_route.mcp_router)
tools_app.include_router(data_route.resource_router)


# ----------------------------------
# 2. FastMCP 인스턴스 생성
# ----------------------------------
# tools_app을 기반으로 MCP 인스턴스 생성
mcp = FastMCP.from_fastapi(
    tools_app,
    name="fisa-mcp", 
    instructions = instructions,
    version="0.1.0")


# ----------------------------------
# 3. 통합 FastAPI 앱 (API 문서 및 MCP 툴의 원본 포함)
# ----------------------------------
all_app = FastAPI(
    title="FISA MCP 통합 서버",
    description="Finance AI Services Agent Server",
    version="0.1.0"
)

# 기존 라우터
all_app.include_router(mcp_route.mcp_router)  # MCP Tools 원본 API
all_app.include_router(data_route.resource_router) # resource 관련 Tool API

# 🎯 Report Agent Tools 라우터 추가
all_app.include_router(report_db_tools.router)
all_app.include_router(report_agent_tools.router)

all_app.include_router(create_mcp_admin_router(mcp))
# all_app.include_router(create_mcp_admin_router(mcp))  # MCP 관리 API

# ----------------------------------
# 4. MCP 서비스 경로 설정
# ----------------------------------
# / 경로에서 MCP의 Open API 스펙과 트랜스포트 API를 제공합니다.
mcp_app = mcp.http_app(
    path="/",
    transport="http",
    stateless_http=False,
    json_response=True
)