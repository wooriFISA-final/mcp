from fastapi import APIRouter

# from server.api.tools import user_tools
from server.api.tools import plan_agent_tools
from server.api.tools import report_agent_tools

mcp_router = APIRouter(
    prefix="/tools",
    tags=["MCP Tools"]
)
# mcp_router.include_router(user_tools.router)
mcp_router.include_router(plan_agent_tools.router)
mcp_router.include_router(report_agent_tools.router)