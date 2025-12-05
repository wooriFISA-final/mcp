# ============================================
# server/mcp_server.py
# ============================================
from fastmcp import FastMCP
from fastapi import FastAPI
# from server.api.tools.user_tools import create_user, get_user
# from server.api.resources.user_resources import get_user_stats, get_all_users_resource
# from server.api.prompts.user_prompts import user_greeting, user_report
from server.routes import data_route
from server.routes import mcp_route


instructions = (
    "이 MCP 서버는 금액 파싱, 지역 정규화, 퍼센트/비율 파싱, 입력 검증, 예·적금 Top3 필터링, 리스크 레벨별 예상 수익률 Top1만 뽑아주는 순수, 부족 자금(shortage_amount) 계산, 복리 기반 투자 시뮬레이션, DB 조회 기능을 제공합니다."
)
# MCP Tools용 앱 (MCP로 변환될 API들)
tools_app = FastAPI()
tools_app.include_router(mcp_route.mcp_router)
tools_app.include_router(data_route.resource_router)


mcp = FastMCP.from_fastapi(
    tools_app,
    name="fisa-mcp", 
    instructions = instructions,
    version="0.1.0")

all_app = FastAPI()
all_app.include_router(mcp_route.mcp_router)  # MCP Tools 원본 API
all_app.include_router(data_route.resource_router) # resource 관련 Tool API
# all_app.include_router(create_mcp_admin_router(mcp))  # MCP 관리 API

mcp_app = mcp.http_app(
    path="/",
    transport="http",
    stateless_http=False,
    json_response=True
)
