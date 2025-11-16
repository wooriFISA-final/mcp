from datetime import datetime
from typing import Optional
from config.logger import get_logger
from fastapi import APIRouter
from server.schemas.user_schema import (
    UserCreateRequest,
    UserGetRequest
)

logger = get_logger(__name__)

# 임시 인메모리 DB
USERS_DB = {}
USER_COUNTER = 0

router = APIRouter(
    prefix="/users" 
)

@router.post(
    "/create_user",
    summary="유저 생성",
    operation_id="create_user",
    description="사용자의 정보(이름, 나이)를 받아 사용자 DB에 생성한다.",
    response_model=dict, # 추후에 형식이 지정되면 그걸로 설정

)
async def create_user(requset:UserCreateRequest) -> dict:
    user_data = {
        "name": requset.name,
        "age": requset.age,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    USERS_DB[requset.name] = user_data
    return {"tool_name": "create_user", "success": True, "user": user_data}

@router.get(
    "/get_user",
    summary="유저 조회",
    operation_id="get_user",
    description="사용자의 이름을 받아 DB에서 유저를 조회한다.",
    response_model=dict, # 추후에 형식이 지정되면 그걸로 설정

)
async def get_user(request:UserGetRequest) -> dict:
    if request.name not in USERS_DB:
        return {"success": False, "error": f"User {request.name} not found", "user": None}
    return {"tool_name": "get_user", "success": True, "user": USERS_DB[request.name]}

