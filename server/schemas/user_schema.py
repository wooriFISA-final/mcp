from pydantic import BaseModel, Field

#user_create schema
class UserCreateRequest(BaseModel):
    name: str = Field(..., description="사용자 이름")
    age: int = Field(..., description="사용자 나이")

class UserGetRequest(BaseModel):
    name: str = Field(..., description="사용자 정보를 조회하기 위한 사용자 ID(이름)")