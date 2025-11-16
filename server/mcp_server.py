# ============================================
# server/mcp_server.py
# ============================================
from fastmcp import FastMCP
from fastapi import FastAPI
from server.api.mcp_admin_routes import create_mcp_admin_router
from server.routes import mcp_route
from server.routes import data_route

instructions = (
    "이 MCP 서버는 ~~~ 조회 기능을 제공합니다."
)
# MCP Tools용 앱 (MCP로 변환될 API들)
tools_app = FastAPI()
tools_app.include_router(mcp_route.mcp_router)
tools_app.include_router(data_route.resource_router)
# all_app.include_router(report_route) -> 추후에 추가 / 수정 예정

# FastAPI의 API 전체를 MCP 도구 세트로 래핑하고 MCP 서버 객체를 생성.
mcp = FastMCP.from_fastapi(
    tools_app,
    name="fisa-mcp", 
    instructions = instructions,
    version="0.1.0")


# 전체 API 앱 (일반 REST API)
all_app = FastAPI()
all_app.include_router(mcp_route.mcp_router)  # MCP Tools 원본 API
all_app.include_router(data_route.resource_router) # resource 관련 Tool API
all_app.include_router(create_mcp_admin_router(mcp))  # MCP 관리 API

# stateless_http=True -> 클라이언트의 요청이 대폭 증가해도 서버를 증설해결 가능
# MCP JSON‑RPC 서브 앱 생성
mcp_app = mcp.http_app(
    path="/",
    transport="http",
    stateless_http=False,
    json_response=True
)

