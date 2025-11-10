from server.tools.user_tools import USERS_DB

async def user_greeting(user_name: str) -> str:
    return f"안녕하세요 {user_name}님! 무엇을 도와드릴까요?"

async def user_report(user_id: str) -> str:
    if user_id not in USERS_DB: return f"{user_id}를 찾을 수 없습니다."
    u = USERS_DB[user_id]
    return f"사용자 {u['name']}({u['id']}) 정보: 이메일 {u['email']}, 나이 {u['age']}, 가입일 {u['created_at']}"
