from fastapi import APIRouter
from server.api.resources import db_tools
from server.api.resources import report_db_tools 
from server.api.tools import report_agent_tools  

resource_router = APIRouter()
resource_router.include_router(db_tools.router)
resource_router.include_router(report_db_tools.router)  
resource_router.include_router(report_agent_tools.router)  