import json
import os
from pathlib import Path
import re
import logging
from datetime import datetime
from fastapi import APIRouter, Body
from typing import Dict, Any, List, Optional

import pandas as pd  # ✅ filter_top_savings_products에서 사용

# 라우터 설정
router = APIRouter(
    prefix="/input",  # API 엔드포인트 기본 경로
    tags=["PlanInput & Validation Tools"]  # Swagger UI용 카테고리 표시
)

logger = logging.getLogger(__name__)


# 1. 금액 파싱 Tool
@router.post(
    "/parse_currency",
    summary="한국어 금액 단위 변환",
    operation_id="parse_currency",
    description=(
        "한국어 금액 단위(억, 천만, 만 등)를 원 단위 정수로 변환합니다.\n\n"
        "예:\n"
        "- '3억 5천만' → 350000000\n"
        "- '1200만' → 12000000"
    ),
    response_model=dict,
)
async def api_parse_currency(value: Any = Body(..., embed=True)) -> dict:
    # 엔드포인트 내부에 파서 함수를 중첩 정의
    def _parse_korean_currency(v: Any) -> int:
        """'3억 5천' 같은 금액 표현을 정수(원)로 변환"""
        if v is None or v == "":
            return 0
        if isinstance(v, (int, float)):
            return int(v)

        text = str(v).strip().replace(",", "").replace(" ", "")
        if text == "":
            return 0

        # 숫자만이면 그대로
        if re.fullmatch(r"\d+", text):
            return int(text)

        total = 0.0
        for pattern, multiplier in [
            (r"(\d+(?:\.\d+)?)억", 100_000_000),
            (r"(\d+(?:\.\d+)?)천만", 10_000_000),
            (r"(\d+(?:\.\d+)?)백만", 1_000_000),
            (r"(\d+(?:\.\d+)?)만", 10_000),
        ]:
            m = re.search(pattern, text)
            if m:
                total += float(m.group(1)) * multiplier

        if total == 0:
            # 단위가 없는데 숫자+문자 혼합이면 숫자만 추출
            digits = re.sub(r"[^0-9]", "", text)
            try:
                return int(float(digits)) if digits else 0
            except ValueError:
                return 0

        return int(total)

    try:
        parsed = _parse_korean_currency(value)
        return {
            "tool_name": "parse_currency",
            "success": True,
            "parsed": parsed,
        }
    except Exception as e:
        logger.exception("parse_currency 실패")
        return {
            "tool_name": "parse_currency",
            "success": False,
            "error": str(e),
            "parsed": 0,
        }


# 2. 헬스 체크 엔드포인트
@router.get(
    "/health",
    summary="상태 점검(Health Check)",
    operation_id="plan_health",
    description=(
        "PlanInput 관련 툴 서버의 동작 상태를 확인합니다.\n\n"
        "출력 필드:\n"
        "- success: 헬스 체크 성공 여부(Boolean)\n"
        "- llm_model: 사용 중인 LLM 모델명 (환경변수 PLAN_LLM, 기본값 'qwen3:8b')\n\n"
        "응답 예시:\n"
        '{"success": true, "llm_model": "qwen3:8b"}'
    ),
    response_model=dict,
)
async def api_health() -> dict:
    try:
        llm_model = os.getenv("PLAN_LLM", "qwen3:8b")
        return {
            "tool_name": "plan_health",
            "success": True,
            "llm_model": llm_model,
        }
    except Exception as e:
        logger.exception("health 실패")
        return {
            "tool_name": "plan_health",
            "success": False,
            "error": str(e),
        }


# 3. 지역 정규화 Tool
@router.post(
    "/normalize_location",
    summary="지역명 정규화",
    operation_id="normalize_location",
    description=(
        "자유 형식의 지역명을 표준 행정구역명으로 정규화합니다.\n\n"
        "규칙 예시:\n"
        "- '서울'이 포함되면 '서울특별시 {구}' 형태로 보정\n"
        "- 광역시는 '○○광역시', 도는 '○○도'로 표기\n\n"
        "입력/출력 예시:\n"
        "- '서울 동작구' → '서울특별시 동작구'\n"
        "- '부산 해운대구' → '부산광역시 해운대구'"
    ),
    response_model=dict,
)
async def normalize_location(location: str = Body(..., embed=True)) -> dict:
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
        return {
            "tool_name": "normalize_location",
            "success": True,
            "normalized": normalized,
        }
    except Exception as e:
        logger.error(f"normalize_location Error: {e}")
        return {
            "tool_name": "normalize_location",
            "success": False,
            "error": str(e),
            "normalized": location,
        }


# 4. 퍼센트/비율 파싱 Tool
@router.post(
    "/parse_ratio",
    summary="비율(%) 정수 변환",
    operation_id="parse_ratio",
    description=(
        "퍼센트(%)가 포함되었든 아니든, 비율 값을 정수로 정규화합니다.\n\n"
        "입력/출력 예시:\n"
        "- '30%' → 30\n"
        "- '15'  → 15\n"
        "- ' 40 % ' → 40\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부(Boolean)\n"
        "- ratio: 정수 비율 값"
    ),
    response_model=dict,
)
async def parse_ratio(value: str = Body(..., embed=True)) -> dict:
    """'30%' 또는 '20' 같은 입력을 정수 비율로 변환"""
    try:
        if not value:
            return {
                "tool_name": "parse_ratio",
                "success": False,
                "ratio": 0,
            }
        ratio = int(str(value).replace("%", "").strip())
        return {
            "tool_name": "parse_ratio",
            "success": True,
            "ratio": ratio,
        }
    except Exception as e:
        logger.error(f"parse_ratio Error: {e}")
        return {
            "tool_name": "parse_ratio",
            "success": False,
            "error": str(e),
            "ratio": 0,
        }


# 5. 입력 검증 Tool (input + validation 통합)
@router.post(
    "/validate_input_data",
    summary="주택 계획 입력값 검증·정규화",
    operation_id="validate_input_data",
    description=(
        "입력된 원시(raw) 데이터를 받아 **누락 필드 점검** 후, "
        "**금액·비율·지역** 값을 표준 형태로 정규화합니다. (DB/시세조회 미포함)\n\n"
        "입력 필드 예시:\n"
        "- data.initial_prop: 초기 자산 (예: '3천만', 30000000)\n"
        "- data.hope_location: 희망 지역 (예: '서울 동작구')\n"
        "- data.hope_price: 희망 가격 (예: '7억', 700000000)\n"
        "- data.hope_housing_type: 주택 유형 (예: '아파트')\n"
        "- data.income_usage_ratio: 월급 사용 비율 (예: '30%', 30)\n\n"
        "출력 필드:\n"
        "- status: 'success' | 'incomplete' | 'error'\n"
        "- data: 정규화된 결과 (success일 때)\n"
        "- missing_fields: 누락된 필드 목록 (incomplete일 때)"
    ),
    response_model=dict,
)
async def validate_input_data(payload: Dict[str, Any] = Body(...)) -> dict:
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
            return {
                "tool_name": "validate_input_data",
                "success": False,
                **result,
            }

        # 각 필드별 정규화 수행
        from fastapi.encoders import jsonable_encoder

        cur1 = await api_parse_currency(data.get("initial_prop", "0"))
        cur2 = await api_parse_currency(data.get("hope_price", "0"))
        ratio = await parse_ratio(data.get("income_usage_ratio", "0"))
        loc = await normalize_location(data.get("hope_location", ""))

        # 정규화 완료된 결과 구성
        result["data"] = jsonable_encoder({
            "initial_prop": cur1.get("parsed", 0),
            "hope_location": loc.get("normalized", data.get("hope_location", "")),
            "hope_price": cur2.get("parsed", 0),
            "hope_housing_type": data.get("hope_housing_type"),
            "income_usage_ratio": ratio.get("ratio", 0),
            "validation_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        return {
            "tool_name": "validate_input_data",
            "success": True,
            **result,
        }

    except Exception as e:
        logger.error(f"validate_input_data Error: {e}")
        return {
            "tool_name": "validate_input_data",
            "success": False,
            "status": "error",
            "message": str(e),
            "data": {},
            "missing_fields": [],
        }


# 6. 예·적금 Top3 필터링 Tool (CSV + 조건 필터링)
@router.post(
    "/filter_top_products",
    summary="예·적금 Top3 상품 필터링",
    operation_id="filter_top_savings_products",
    description=(
        "사용자의 나이, 첫거래 여부, 목표 기간(개월)을 기준으로 예·적금 상품 CSV에서 "
        "조건에 맞는 상품을 필터링하고, 각각 **Top3 (금리 기준)**를 반환합니다.\n\n"
        "입력 필드 예시:\n"
        "- user_data.age: 사용자 나이 (예: 32)\n"
        "- user_data.is_first_customer: 첫 거래 여부 (예: true/false)\n"
        "- user_data.period_goal_months: 목표 기간(개월) (예: 12)\n"
        "- csv_file_path (선택): CSV 경로 (미지정 시 기본값 사용)\n\n"
        "CSV 컬럼 예시:\n"
        "- product_type: '예금' 또는 '적금'\n"
        "- condition_min_age: 가입 최소 나이\n"
        "- condition_first_customer: 첫 거래 고객 전용 여부(Boolean)\n"
        "- min_term, max_term: 가입 가능 최소/최대 기간(개월)\n"
        "- max_rate: 최대 금리\n"
        "- name, description 등 기타 정보\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부(Boolean)\n"
        "- top_deposits: 조건에 맞는 예금 Top3 리스트\n"
        "- top_savings: 조건에 맞는 적금 Top3 리스트\n"
        "- meta: 사용된 CSV 경로, 필터링된 상품 수 등 부가정보"
    ),
    response_model=dict,
)
async def filter_top_savings_products(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    예·적금 CSV에서 사용자 조건에 맞는 상품을 필터링하고,
    예금/적금 각각 max_rate 기준 Top3를 반환하는 Tool.
    """
    try:
        user_data: Dict[str, Any] = payload.get("user_data", {}) or {}
        csv_path: str = payload.get("csv_file_path") or ""

        # 1) CSV 경로 설정 (없거나 존재하지 않으면 기본값 사용)
        if not csv_path or not os.path.exists(csv_path):
            logger.warning(
                "csv_file_path가 전달되지 않았거나 존재하지 않아 기본 경로를 사용합니다. "
                f"(입력값: {csv_path})"
            )
            default_path = Path(__file__).resolve().parents[2] / "data" / "saving_data.csv"
            csv_path = str(default_path)

        if not os.path.exists(csv_path):
            msg = f"CSV 파일을 찾을 수 없습니다: {csv_path}"
            logger.error(msg)
            return {
                "tool_name": "filter_top_savings_products",
                "success": False,
                "error": msg,
                "top_deposits": [],
                "top_savings": [],
            }

        # 2) CSV 로드
        try:
            all_products_df = pd.read_csv(csv_path)
        except Exception as e:
            msg = f"CSV 로드 실패 ({csv_path}): {e}"
            logger.error(msg)
            return {
                "tool_name": "filter_top_savings_products",
                "success": False,
                "error": msg,
                "top_deposits": [],
                "top_savings": [],
            }

        # 3) 공통 필터 기준
        age = int(user_data.get("age", 0) or 0)
        is_first_customer = bool(user_data.get("is_first_customer", True))
        period = int(user_data.get("period_goal_months", 12) or 12)

        # ============================
        # 3-1) 예금 필터링
        # ============================
        try:
            deposits_df = all_products_df[all_products_df["product_type"] == "예금"].copy()

            # 나이 조건
            if "condition_min_age" in deposits_df.columns:
                deposits_df = deposits_df[deposits_df["condition_min_age"] <= age]

            # 첫거래 조건
            if "condition_first_customer" in deposits_df.columns and not is_first_customer:
                deposits_df = deposits_df[deposits_df["condition_first_customer"] == False]

            # 기간 조건
            if {"min_term", "max_term"}.issubset(deposits_df.columns):
                deposits_df = deposits_df[
                    (deposits_df["min_term"] <= period)
                    & (deposits_df["max_term"] >= period)
                ]

            # 금리 기준 Top3
            if "max_rate" in deposits_df.columns:
                deposits_df = deposits_df.sort_values(by="max_rate", ascending=False)

            top_3_deposits = deposits_df.head(3)
            top_deposits = top_3_deposits.to_dict(orient="records")
        except Exception as e:
            logger.error(f"예금 필터링 중 오류: {e}")
            top_deposits = []

        # ============================
        # 3-2) 적금 필터링
        # ============================
        try:
            savings_df = all_products_df[all_products_df["product_type"] == "적금"].copy()

            # 나이 조건
            if "condition_min_age" in savings_df.columns:
                savings_df = savings_df[savings_df["condition_min_age"] <= age]

            # 첫거래 조건
            if "condition_first_customer" in savings_df.columns and not is_first_customer:
                savings_df = savings_df[savings_df["condition_first_customer"] == False]

            # 기간 조건
            if {"min_term", "max_term"}.issubset(savings_df.columns):
                savings_df = savings_df[
                    (savings_df["min_term"] <= period)
                    & (savings_df["max_term"] >= period)
                ]

            # 금리 기준 Top3
            if "max_rate" in savings_df.columns:
                savings_df = savings_df.sort_values(by="max_rate", ascending=False)

            top_3_savings = savings_df.head(3)
            top_savings = top_3_savings.to_dict(orient="records")
        except Exception as e:
            logger.error(f"적금 필터링 중 오류: {e}")
            top_savings = []

        return {
            "tool_name": "filter_top_savings_products",
            "success": True,
            "top_deposits": top_deposits,
            "top_savings": top_savings,
            "meta": {
                "csv_path": csv_path,
                "user_data": user_data,
                "count_deposits": len(top_deposits),
                "count_savings": len(top_savings),
            },
        }

    except Exception as e:
        logger.error(f"filter_top_savings_products Error: {e}", exc_info=True)
        return {
            "tool_name": "filter_top_savings_products",
            "success": False,
            "error": str(e),
            "top_deposits": [],
            "top_savings": [],
        }


# 7. 리스크 레벨별 예상 수익률 Top1만 뽑아주는 순수 Tool
@router.post(
    "/select_top_by_risk",
    summary="리스크 레벨별 펀드 Top1 선별",
    operation_id="select_top_funds_by_risk",
    description=(
        "펀드 원시 데이터(Raw Fund Data)를 입력받아, "
        "`risk_level`별로 `expected_return`(예상 수익률)이 가장 높은 상품을 "
        "**각각 1개씩** 선별하여 반환합니다.\n\n"
        "입력 방법은 두 가지 중 하나입니다.\n"
        "1) fund_data (권장):\n"
        '   - body.fund_data 에 펀드 목록 리스트를 직접 전달\n'
        "2) fund_data_path:\n"
        "   - body.fund_data_path 에 JSON 파일 경로를 전달\n"
        "   - 미지정 시 기본 경로(fund_data.json)를 사용합니다.\n\n"
        "각 펀드 항목 예시 필드:\n"
        "- risk_level: '높은 위험', '중간 위험', '낮은 위험' 등\n"
        "- product_name (또는 name): 상품명\n"
        "- expected_return: '12.5%' 또는 10.0 등\n"
        "- description: 상품 설명\n\n"
        "출력 필드:\n"
        "- success: 처리 성공 여부(Boolean)\n"
        "- recommendations: 리스크 레벨별 Top1 펀드 목록\n"
        "- meta: 사용된 데이터 개수 등 부가 정보"
    ),
    response_model=dict,
)
async def select_top_funds_by_risk(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    리스크 레벨별로 예상 수익률이 가장 높은 펀드 상품을 1개씩 선별하는 Tool.
    (LLM, LangGraph 사용 X / 순수 파이썬 로직만 사용)
    """

    # -----------------------------
    # ① 내부 유틸: 펀드 데이터 로드
    # -----------------------------
    def _load_fund_data(
        fund_data: Optional[List[Dict[str, Any]]] = None,
        fund_data_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        1순위: fund_data(바디 내 리스트) 사용
        2순위: fund_data_path(파일 경로)에서 JSON 로드
        """
        # 1) 바디에 fund_data가 직접 들어온 경우
        if fund_data:
            if isinstance(fund_data, list):
                return fund_data
            else:
                raise ValueError("fund_data는 리스트 형식이어야 합니다.")

        # 2) 파일 경로 기반 로드
        path = fund_data_path or ""
        if not path or not os.path.exists(path):
            logger.warning(
                "fund_data_path가 전달되지 않았거나 존재하지 않아 기본 경로를 사용합니다. "
                f"(입력값: {path})"
            )
            # 🔁 기본 경로(환경에 맞게 수정 가능)
            default_path = Path(__file__).resolve().parents[2] / "fund_data.json"
            path = str(default_path)

        if not os.path.exists(path):
            raise FileNotFoundError(f"펀드 데이터 파일을 찾을 수 없습니다: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("펀드 데이터 JSON의 최상위 구조는 리스트여야 합니다.")

        return data

    # -----------------------------
    # ② 내부 유틸: 기대수익률 파싱
    # -----------------------------
    def _parse_expected_return(value: Any) -> float:
        """
        expected_return 값을 숫자로 파싱.
        예:
        - '12.5%' -> 12.5
        - '8'     -> 8.0
        - 0.08    -> 0.08 (그대로)
        """
        if value is None:
            return 0.0

        # 숫자형이면 float로
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()
        if text.endswith("%"):
            text = text[:-1].strip()

        try:
            return float(text)
        except ValueError:
            return 0.0

    try:
        fund_data_in_body: Optional[List[Dict[str, Any]]] = payload.get("fund_data")
        fund_data_path: Optional[str] = payload.get("fund_data_path")

        # 1) 데이터 로드
        funds = _load_fund_data(fund_data_in_body, fund_data_path)

        if not funds:
            return {
                "tool_name": "select_top_funds_by_risk",
                "success": False,
                "error": "펀드 데이터가 비어 있습니다.",
                "recommendations": [],
            }

        # 2) risk_level 그룹별 최고 expected_return 상품 선별
        best_by_risk: Dict[str, Dict[str, Any]] = {}

        for item in funds:
            risk_level = item.get("risk_level")
            if not risk_level:
                # risk_level 없는 항목은 스킵
                continue

            score = _parse_expected_return(item.get("expected_return"))
            current_best = best_by_risk.get(risk_level)

            # 처음이거나, 기존보다 수익률이 높으면 갱신
            if current_best is None or _parse_expected_return(current_best.get("expected_return")) < score:
                best_by_risk[risk_level] = item

        # 3) 결과 리스트 구성
        recommendations: List[Dict[str, Any]] = []
        for risk_level, item in best_by_risk.items():
            recommendations.append(
                {
                    "risk_level": risk_level,
                    "product_name": item.get("product_name") or item.get("name"),
                    "expected_return": item.get("expected_return"),
                    "description": item.get("description"),
                    # summary_for_beginner는 이 Tool이 아니라,
                    # 나중에 LLM Agent에서 생성하도록 남겨둠.
                }
            )

        # expected_return 내림차순 정렬 (보기 편하게)
        recommendations.sort(
            key=lambda x: _parse_expected_return(x.get("expected_return")),
            reverse=True,
        )

        return {
            "tool_name": "select_top_funds_by_risk",
            "success": True,
            "recommendations": recommendations,
            "meta": {
                "total_input_funds": len(funds),
                "unique_risk_levels": len(best_by_risk),
                "source": "fund_data_in_body" if fund_data_in_body else "fund_data_path",
                "fund_data_path": fund_data_path,
            },
        }

    except FileNotFoundError as e:
        logger.error(f"select_top_funds_by_risk FileNotFoundError: {e}")
        return {
            "tool_name": "select_top_funds_by_risk",
            "success": False,
            "error": str(e),
            "recommendations": [],
        }
    except Exception as e:
        logger.error(f"select_top_funds_by_risk Error: {e}", exc_info=True)
        return {
            "tool_name": "select_top_funds_by_risk",
            "success": False,
            "error": str(e),
            "recommendations": [],
        }


# 8. 부족 자금(shortage_amount) 계산 Tool
@router.post(
    "/calc_shortage",
    summary="주택 자금 부족액 계산",
    operation_id="calc_shortage_amount",
    description=(
        "희망 주택 가격, 예상 대출 금액, 보유 자산을 입력받아 "
        "**부족 자금(Shortage Amount)** 을 계산합니다.\n\n"
        "계산식:\n"
        "- shortage_amount = max(0, hope_price - (loan_amount + initial_prop))\n\n"
        "입력 예시:\n"
        "- hope_price: 800000000  (희망 주택 가격)\n"
        "- loan_amount: 400000000 (예상 대출 금액)\n"
        "- initial_prop: 200000000 (보유 자산)\n\n"
        "출력:\n"
        "- shortage_amount: 200000000"
    ),
    response_model=dict,
)
async def calc_shortage_amount(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    희망 주택 가격, 대출 금액, 보유 자산을 기반으로 부족 자금을 계산하는 Tool.
    (DB 업데이트 없음, 순수 계산 전용)
    """
    # 내부 유틸: 안전한 정수 변환
    def _to_int(v: Any) -> int:
        try:
            if v is None:
                return 0
            return int(float(v))
        except Exception:
            return 0

    try:
        hope_price = _to_int(payload.get("hope_price"))
        loan_amount = _to_int(payload.get("loan_amount"))
        initial_prop = _to_int(payload.get("initial_prop"))

        shortage = max(0, hope_price - (loan_amount + initial_prop))

        return {
            "tool_name": "calc_shortage_amount",
            "success": True,
            "shortage_amount": shortage,
            "inputs": {
                "hope_price": hope_price,
                "loan_amount": loan_amount,
                "initial_prop": initial_prop,
            },
        }
    except Exception as e:
        logger.error(f"calc_shortage_amount Error: {e}", exc_info=True)
        return {
            "tool_name": "calc_shortage_amount",
            "success": False,
            "error": str(e),
            "shortage_amount": 0,
        }


# 9. 복리 기반 투자 시뮬레이션 Tool
@router.post(
    "/simulate_investment",
    summary="복리 기반 투자 시뮬레이션",
    operation_id="simulate_combined_investment",
    description=(
        "부족 자금을 채우기 위한 **예금/적금 + 펀드** 복합 투자 시뮬레이션을 수행합니다.\n\n"
        "입력값:\n"
        "- shortage: 채워야 할 부족 금액 (원 단위)\n"
        "- available_assets: 현재 투자에 투입 가능한 자산 (원 단위)\n"
        "- monthly_income: 월 소득 (원 단위)\n"
        "- income_usage_ratio: 월 소득 중 투자에 사용할 비율 (%)\n"
        "- saving_yield: 예금/적금 연 수익률 (%)\n"
        "- fund_yield: 펀드 연 수익률 (%)\n"
        "- saving_ratio: 투자 비중 중 예금/적금 비율 (0~1)\n"
        "- fund_ratio: 투자 비중 중 펀드 비율 (0~1)\n\n"
        "계산 방식(요약):\n"
        "- 초기 자산을 saving_ratio / fund_ratio 비율로 나눠 각각 투자\n"
        "- 매월 투자금 = monthly_income × (income_usage_ratio / 100)\n"
        "- 매월 예금/적금·펀드 각각에 비율대로 적립 + 월복리 적용\n"
        "- 총 자산이 shortage 이상이 되는 시점까지 최대 600개월(50년) 반복\n\n"
        "출력값(simulation):\n"
        "- months_needed: 부족금 달성까지 필요한 개월 수 (최대 600)\n"
        "- total_balance: 시뮬레이션 종료 시점의 총 자산\n"
        "- monthly_invest: 매월 투자 금액\n"
        "- saving_ratio, fund_ratio: 사용된 비중(그대로 반환)"
    ),
    response_model=dict,
)
async def simulate_investment(
    payload: Dict[str, Any] = Body(...)
) -> dict:
    """
    예금/적금 + 펀드 복합 투자를 단순 월복리 모델로 시뮬레이션하는 Tool.
    (DB / LLM 사용 없음)
    """

    # 내부 유틸: 숫자 변환
    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    def _to_int(v: Any, default: int = 0) -> int:
        try:
            if v is None:
                return default
            return int(float(v))
        except Exception:
            return default

    # 내부 유틸: 시뮬레이션 로직
    def _simulate(
        shortage: int,
        available_assets: int,
        monthly_income: float,
        income_usage_ratio: float,
        saving_yield: float,
        fund_yield: float,
        saving_ratio: float,
        fund_ratio: float,
    ) -> Dict[str, Any]:
        # 이미 부족금이 없으면 바로 종료
        if shortage <= 0:
            return {
                "months_needed": 0,
                "total_balance": available_assets,
                "monthly_invest": int(monthly_income * (income_usage_ratio / 100)),
                "saving_ratio": saving_ratio,
                "fund_ratio": fund_ratio,
            }

        # 초기 자산 분배
        init_saving = available_assets * saving_ratio
        init_fund = available_assets * fund_ratio

        monthly_invest = monthly_income * (income_usage_ratio / 100.0)
        saving_monthly = monthly_invest * saving_ratio
        fund_monthly = monthly_invest * fund_ratio

        total_balance = init_saving + init_fund
        months = 0

        # 최대 600개월(50년) 제한
        while total_balance < shortage and months < 600:
            months += 1
            # 월복리 적용 (연 수익률 -> 월 수익률 = r/12)
            init_saving = (init_saving + saving_monthly) * (1 + saving_yield / 100.0 / 12.0)
            init_fund = (init_fund + fund_monthly) * (1 + fund_yield / 100.0 / 12.0)
            total_balance = init_saving + init_fund

        return {
            "months_needed": months,
            "total_balance": int(total_balance),
            "monthly_invest": int(monthly_invest),
            "saving_ratio": saving_ratio,
            "fund_ratio": fund_ratio,
        }

    try:
        shortage = _to_int(payload.get("shortage"), 0)
        available_assets = _to_int(payload.get("available_assets"), 0)
        monthly_income = _to_float(payload.get("monthly_income"), 0.0)
        income_usage_ratio = _to_float(payload.get("income_usage_ratio"), 20.0)

        saving_yield = _to_float(payload.get("saving_yield"), 3.0)
        fund_yield = _to_float(payload.get("fund_yield"), 6.0)

        saving_ratio = _to_float(payload.get("saving_ratio"), 0.5)
        fund_ratio = _to_float(payload.get("fund_ratio"), 0.5)

        simulation = _simulate(
            shortage=shortage,
            available_assets=available_assets,
            monthly_income=monthly_income,
            income_usage_ratio=income_usage_ratio,
            saving_yield=saving_yield,
            fund_yield=fund_yield,
            saving_ratio=saving_ratio,
            fund_ratio=fund_ratio,
        )

        return {
            "tool_name": "simulate_combined_investment",
            "success": True,
            "simulation": simulation,
            "inputs": {
                "shortage": shortage,
                "available_assets": available_assets,
                "monthly_income": monthly_income,
                "income_usage_ratio": income_usage_ratio,
                "saving_yield": saving_yield,
                "fund_yield": fund_yield,
                "saving_ratio": saving_ratio,
                "fund_ratio": fund_ratio,
            },
        }

    except Exception as e:
        logger.error(f"simulate_investment Error: {e}", exc_info=True)
        return {
            "tool_name": "simulate_combined_investment",
            "success": False,
            "error": str(e),
            "simulation": None,
        }
