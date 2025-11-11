from datetime import datetime
from typing import Optional
from config.logger import get_logger
import mcp
logger = get_logger(__name__)

# 임시 인메모리 DB
USERS_DB = {}
USER_COUNTER = 0


async def create_user(name: str, email: str, age: int, phone: Optional[str] = None) -> dict:
    global USER_COUNTER
    for user in USERS_DB.values():
        if user["email"] == email:
            return {"success": False, "error": f"Email {email} exists", "user": None}
    USER_COUNTER += 1
    user_id = f"user_{USER_COUNTER}"
    user_data = {
        "id": user_id,
        "name": name,
        "email": email,
        "age": age,
        "phone": phone,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    USERS_DB[user_id] = user_data
    return {"success": True, "user": user_data}

async def get_user(user_id: str) -> dict:
    if user_id not in USERS_DB:
        return {"success": False, "error": f"User {user_id} not found", "user": None}
    return {"success": True, "user": USERS_DB[user_id]}

async def update_user(user_id: str, name: Optional[str] = None, email: Optional[str] = None,
                      age: Optional[int] = None, phone: Optional[str] = None) -> dict:
    if user_id not in USERS_DB:
        return {"success": False, "error": f"User {user_id} not found", "user": None}
    user = USERS_DB[user_id]
    if name: user["name"] = name
    if email: user["email"] = email
    if age: user["age"] = age
    if phone: user["phone"] = phone
    user["updated_at"] = datetime.now().isoformat()
    return {"success": True, "user": user}

async def delete_user(user_id: str) -> dict:
    if user_id not in USERS_DB:
        return {"success": False, "error": f"User {user_id} not found"}
    deleted = USERS_DB.pop(user_id)
    return {"success": True, "deleted_user": deleted}

async def list_users(limit: int = 10, offset: int = 0) -> dict:
    all_users = list(USERS_DB.values())
    return {"success": True, "users": all_users[offset:offset+limit], "total": len(all_users)}

async def search_users(query: str, field: str = "name") -> dict:
    query_lower = query.lower()
    results = [u for u in USERS_DB.values() if query_lower in str(u.get(field, "")).lower()]
    return {"success": True, "users": results, "total": len(results)}
