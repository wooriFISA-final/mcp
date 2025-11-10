from fastmcp import FastMCP
from server.tools.user_tools import create_user, get_user, update_user, delete_user, list_users, search_users
from server.resources.user_resources import get_user_stats, get_all_users_resource
from server.prompts.user_prompts import user_greeting, user_report

mcp = FastMCP(name="user-management-server", version="0.1.0")

# Tools 등록
mcp.tool()(create_user)
mcp.tool()(get_user)
mcp.tool()(update_user)
mcp.tool()(delete_user)
mcp.tool()(list_users)
mcp.tool()(search_users)

# Resources 등록
mcp.resource("user://database/stats")(get_user_stats)
mcp.resource("user://database/all")(get_all_users_resource)

# Prompts 등록
mcp.prompt()(user_greeting)
mcp.prompt()(user_report)
