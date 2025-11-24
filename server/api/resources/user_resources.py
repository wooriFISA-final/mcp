from datetime import datetime
from server.api.tools.user_tools import USERS_DB
from config.logger import get_logger

logger = get_logger(__name__)


async def get_user_stats() -> str:
    logger.info("Fetching user stats")
    total = len(USERS_DB)
    age_groups = {"10대":0,"20대":0,"30대":0,"40대+":0}
    for u in USERS_DB.values():
        age = u.get("age",0)
        if age<20: age_groups["10대"]+=1
        elif age<30: age_groups["20대"]+=1
        elif age<40: age_groups["30대"]+=1
        else: age_groups["40대+"]+=1
    return f"전체 사용자: {total}\n10대:{age_groups['10대']},20대:{age_groups['20대']},30대:{age_groups['30대']},40대+:{age_groups['40대+']}"


async def get_all_users_resource() -> str:
    logger.info("Fetching all users")
    if not USERS_DB: return "등록된 사용자가 없습니다."
    text = ""
    for u in USERS_DB.values():
        text += f"{u['id']} {u['name']} {u['email']} {u['age']}세\n"
    return text
