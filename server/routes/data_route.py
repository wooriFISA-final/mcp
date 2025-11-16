from fastapi import APIRouter
from server.api.resources import db_tools

resource_router = APIRouter()
resource_router.include_router(db_tools.router)
