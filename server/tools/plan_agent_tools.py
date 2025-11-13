import re
import logging
from datetime import datetime
from fastapi import APIRouter
from typing import Dict, Any


# 라우터 설정
router = APIRouter(
    prefix="/input",  # API 엔드포인트 기본 경로
    tags=["PlanInput & Validation Tools"]  # Swagger UI용 카테고리 표시
)

logger = logging.getLogger(__name__)


# ① 금액 파싱 Tool
@router.post(
    "/parse_currency",
    summary="한국어 금액 단위(억, 천만, 만 등)를 원 단위 정수로 변환",
    operation_id="parse_currency",
    description="예: '3억 5천만' → 350000000, '1200만' → 12000000",
    response_model=dict,
)
async def parse_currency(value: str) -> dict:
    """'3억 5천' 같은 금액 표현을 정수(원)로 변환"""
    if not value:
        return {"success": False, "result": 0}
    try:
        text = value.replace(",", "").replace(" ", "")
        total = 0
        # 단위별 숫자 추출 및 합산
        for pattern, multiplier in [
            (r"(\d+(?:\.\d+)?)억", 100_000_000),
            (r"(\d+(?:\.\d+)?)천만", 10_000_000),
            (r"(\d+(?:\.\d+)?)백만", 1_000_000),
            (r"(\d+(?:\.\d+)?)만", 10_000),
        ]:
            match = re.search(pattern, text)
            if match:
                total += float(match.group(1)) * multiplier
        # 숫자만 입력된 경우 처리
        if total == 0:
            total = int(float(re.sub(r"[^0-9]", "", text)))
        return {"success": True, "result": int(total)}
    except Exception as e:
        logger.error(f"parse_currency Error: {e}")
        return {"success": False, "error": str(e), "result": 0}



# ② 지역 정규화 Tool
@router.post(
    "/normalize_location",
    summary="지역명을 정규화",
    operation_id="normalize_location",
    description="예: '서울 동작구' → '서울특별시 동작구', '부산 해운대구' → '부산광역시 해운대구'",
    response_model=dict,
)
async def normalize_location(location: str) -> dict:
    """간단한 지역명 매핑"""
    try:
        mapping = {
            "서울 동작구": "서울특별시 동작구",
            "서울 마포구": "서울특별시 마포구",
            "서울 송파구": "서울특별시 송파구",
            "부산 해운대구": "부산광역시 해운대구",
            "대구 수성구": "대구광역시 수성구",
        }
        normalized = mapping.get(location.strip(), location)
        return {"success": True, "normalized": normalized}
    except Exception as e:
        logger.error(f"normalize_location Error: {e}")
        return {"success": False, "error": str(e), "normalized": location}


# ③ 퍼센트/비율 파싱 Tool
@router.post(
    "/parse_ratio",
    summary="퍼센트(%) 문자열을 정수 비율로 변환",
    operation_id="parse_ratio",
    description="예: '30%' → 30, '15' → 15",
    response_model=dict,
)
async def parse_ratio(value: str) -> dict:
    """'30%' 또는 '20' 같은 입력을 정수 비율로 변환"""
    try:
        if not value:
            return {"success": False, "ratio": 0}
        ratio = int(str(value).replace("%", "").strip())
        return {"success": True, "ratio": ratio}
    except Exception as e:
        logger.error(f"parse_ratio Error: {e}")
        return {"success": False, "error": str(e), "ratio": 0}



# ④ 입력 검증 Tool (input + validation 통합)
@router.post(
    "/validate_input_data",
    summary="입력된 주택 계획 데이터를 검증 및 정규화",
    operation_id="validate_input_data",
    description="""
    입력된 raw 데이터를 받아 누락된 필드를 확인하고,
    금액·비율·지역 정보를 정규화합니다.
    """,
    response_model=dict,
)
async def validate_input_data(payload: Dict[str, Any]) -> dict:
    """
    전체 입력 데이터의 누락 필드를 검사하고,
    금액·비율·지역 정보를 표준화하여 반환.
    """
    try:
        data = payload.get("data", {})
        result = {
            "status": "success",
            "data": {},
            "missing_fields": [],
        }

        # 필수 입력 필드 정의
        required_fields = [
            "initial_prop", "hope_location", "hope_price", "hope_housing_type", "income_usage_ratio"
        ]

        # 누락 필드 검증
        for field in required_fields:
            value = data.get(field)
            if value in [None, "", 0, "0"]:
                result["missing_fields"].append(field)

        # 필드 누락 시 즉시 반환
        if result["missing_fields"]:
            result["status"] = "incomplete"
            return result

        # 각 필드별 정규화 수행
        from fastapi.encoders import jsonable_encoder
        cur1 = await parse_currency(data.get("initial_prop", "0"))
        cur2 = await parse_currency(data.get("hope_price", "0"))
        ratio = await parse_ratio(data.get("income_usage_ratio", "0"))
        loc = await normalize_location(data.get("hope_location", ""))

        # 정규화 완료된 결과 구성
        result["data"] = jsonable_encoder({
            "initial_prop": cur1.get("result"),
            "hope_location": loc.get("normalized"),
            "hope_price": cur2.get("result"),
            "hope_housing_type": data.get("hope_housing_type"),
            "income_usage_ratio": ratio.get("ratio"),
            "validation_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        return result

    except Exception as e:
        logger.error(f"validate_input_data Error: {e}")
        return {"status": "error", "message": str(e)}
