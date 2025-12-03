## schemas/report_schemas.py

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional


# ----------------------------------------------------------------------
# 1. DB 조회/저장 Tool 입력/출력 스키마
# ----------------------------------------------------------------------

# 1.1 사용자 상세 금융/신용 정보 조회 Tool
class MemberDetailsInput(BaseModel):
    user_id: int = Field(..., description="조회할 사용자의 고유 ID")

class MemberDetailsOutput(BaseModel):
    annual_salary: Optional[int] = Field(None, description="연봉")
    total_debt: Optional[int] = Field(None, description="총 부채")
    credit_score: Optional[int] = Field(None, description="신용 점수")
    has_house: Optional[bool] = Field(None, description="주택 보유 여부")

# 1.2 특정 월의 원시 소비 데이터 조회 Tool
class ConsumeDataRawInput(BaseModel):
    user_id: int = Field(..., description="조회할 사용자의 고유 ID")
    dates: List[str] = Field(..., description="조회할 월(YYYY-MM-DD 형식의 목록)")

# 1.3 가장 최근 레포트 요약 데이터 조회 Tool
class RecentReportSummaryInput(BaseModel):
    member_id: int = Field(..., description="조회할 멤버의 고유 ID")

class RecentReportSummaryOutput(BaseModel):
    annual_salary: Optional[int] = Field(None, description="직전 보고서의 연봉")
    credit_score: Optional[int] = Field(None, description="직전 보고서의 신용 점수")
    report_date: Optional[str] = Field(None, description="직전 보고서 작성일")

# 1.4 사용자의 보유 투자 상품 목록 조회 Tool
class UserProductsInput(BaseModel):
    user_id: int = Field(..., description="조회할 사용자의 고유 ID")

# 1.5 월간 통합 보고서 DB 저장 Tool
class SaveMonthlyReportInput(BaseModel):
    member_id: int = Field(..., description="보고서 대상 멤버 ID")
    report_date: str = Field(..., description="보고서 기준 날짜 (YYYY-MM-DD)")
    report_text: str = Field(..., description="최종 생성된 보고서 텍스트 본문")
    metadata: Dict[str, Any] = Field(..., description="보고서 생성에 사용된 메타데이터 JSON")

# ----------------------------------------------------------------------
# 2. LLM/Processing Tool 입력/출력 스키마
# ----------------------------------------------------------------------

# 2.1 월별 소비 데이터 비교 분석 및 군집 생성 Tool
class AnalyzeSpendingInput(BaseModel):
    consume_records: List[Dict[str, Any]] = Field(..., description="2개월 이상의 원시 소비 데이터 레코드 목록")
    member_data: Dict[str, Any] = Field(..., description="사용자 연봉, 부채 등 상세 정보")
    ollama_model: Optional[str] = Field(None, description="사용할 Ollama 모델 이름")

class AnalyzeSpendingOutput(BaseModel):
    report: str = Field(..., description="LLM이 생성한 소비 분석 보고서 및 조언")
    cluster_nickname: str = Field(..., description="LLM이 부여한 소비 군집 별명")

# 2.2 최종 보고서 3줄 요약 생성 Tool
class FinalSummaryInput(BaseModel):
    report_content: str = Field(..., description="통합 보고서 본문 전체 텍스트")

class FinalSummaryOutput(BaseModel):
    summary: str = Field(..., description="보고서의 3줄 핵심 요약")

# 2.3 RAG/투자 분석 (미완성) Tool 입력/출력 스키마
class ToolSkippedOutput(BaseModel):
    success: bool = Field(False, description="항상 False")
    error: str = Field(..., description="에러 메시지")

# 3. 정책 변동 사항
# 🎯 RAG 검색 입력 스키마 정의
class PolicyRAGSearchInput(BaseModel):
    user_query: str

# 정책 비교 아웃풋
class PolicyRAGSearchOutput(BaseModel):
    tool_name: str
    success: bool
    context: Optional[str] = None
    error: Optional[str] = None