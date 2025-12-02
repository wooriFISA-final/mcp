import os
import logging
from typing import List
from fastapi import APIRouter, HTTPException
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from pydantic import BaseModel
from datetime import datetime

load_dotenv()
logger = logging.getLogger(__name__)

# DB 연결
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")

try:
    engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")
    logger.info("✅ Reports API DB Engine 생성 완료")
except Exception as e:
    logger.error(f"❌ DB Engine 생성 실패: {e}")
    engine = None

router = APIRouter(
    prefix="/reports",
    tags=["Reports API"]
)

class ReportRead(BaseModel):
    report_id: int
    user_id: int
    create_at: str
    consume_report: str | None = None
    cluster_nickname: str | None = None
    change_analysis_report: str | None = None
    profit_analysis_report: str | None = None
    policy_analysis_report: str | None = None
    threelines_summary: str | None = None
    consume_analysis_summary: str | None = None
    spend_chart_json: str | None = None
    change_raw_changes: str | None = None
    policy_changes: str | None = None
    net_profit: float | None = None
    profit_rate: float | None = None
    # 🆕 투자 그래프 데이터 필드 추가
    trend_chart_json: str | None = None
    fund_comparison_json: str | None = None

    class Config:
        from_attributes = True

@router.get("/", response_model=List[ReportRead])
async def get_all_reports():
    """모든 리포트 조회"""
    logger.info("📊 리포트 목록 조회 요청")
    
    if engine is None:
        logger.error("DB 엔진이 없습니다")
        raise HTTPException(status_code=500, detail="DB 연결 오류")
    
    try:
        with engine.connect() as conn:
            query = text("SELECT * FROM reports ORDER BY create_at DESC")
            result = conn.execute(query).mappings().all()
            
            logger.info(f"✅ {len(result)}개의 리포트 조회 완료")
            
            reports = []
            for row in result:
                report_dict = dict(row)
                # create_at을 문자열로 변환
                if 'create_at' in report_dict and report_dict['create_at']:
                    if isinstance(report_dict['create_at'], datetime):
                        report_dict['create_at'] = report_dict['create_at'].strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        report_dict['create_at'] = str(report_dict['create_at'])
                reports.append(report_dict)
            
            return reports
    except Exception as e:
        logger.error(f"❌ 리포트 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{report_id}", response_model=ReportRead)
async def get_report(report_id: int):
    """특정 리포트 조회"""
    logger.info(f"📊 리포트 {report_id} 조회 요청")
    
    if engine is None:
        raise HTTPException(status_code=500, detail="DB 연결 오류")
    
    try:
        with engine.connect() as conn:
            query = text("SELECT * FROM reports WHERE report_id = :id")
            result = conn.execute(query, {"id": report_id}).mappings().first()
            
            if not result:
                logger.warning(f"⚠️ 리포트 {report_id}를 찾을 수 없습니다")
                raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다")
            
            report_dict = dict(result)
            if 'create_at' in report_dict and report_dict['create_at']:
                if isinstance(report_dict['create_at'], datetime):
                    report_dict['create_at'] = report_dict['create_at'].strftime("%Y-%m-%d %H:%M:%S")
                else:
                    report_dict['create_at'] = str(report_dict['create_at'])
            
            logger.info(f"✅ 리포트 {report_id} 조회 완료")
            return report_dict
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 리포트 조회 오류: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
