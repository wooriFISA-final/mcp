from datetime import datetime
from typing import Optional
from config.logger import get_logger
from fastapi import APIRouter

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
async def create_user(name: str, age: int) -> dict:
    global USER_COUNTER
    USER_COUNTER += 1
    user_id = f"user_{USER_COUNTER}"
    user_data = {
        "id": user_id,
        "name": name,
        "age": age,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    USERS_DB[user_id] = user_data
    return {"success": True, "user": user_data}

@router.get(
    "/get_user",
    summary="유저 조회",
    operation_id="get_user",
    description="사용자의 이름을 받아 DB에서 유저를 조회한다.",
    response_model=dict, # 추후에 형식이 지정되면 그걸로 설정

)
async def get_user(name: str) -> dict:
    if name not in USERS_DB:
        return {"success": False, "error": f"User {name} not found", "user": None}
    return {"success": True, "user": USERS_DB[name]}

