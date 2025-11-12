from fastapi import APIRouter

from server.api.tools import user_tools
from server.api.tools import report_tools
from server.api.tools import plan_tools


mcp_router = APIRouter(
    prefix="/tools",
    tags=["MCP Tools"]
)
mcp_router.include_router(user_tools.router)
mcp_router.include_router(report_tools.router)
mcp_router.include_router(plan_tools.router)
