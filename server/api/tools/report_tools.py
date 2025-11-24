"""report agents tool 모음"""
from fastapi import APIRouter

router = APIRouter(
    prefix="/report_tools",
    tags=["Report Tools"]
)