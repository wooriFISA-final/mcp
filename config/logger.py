import logging
import logging.handlers
from pathlib import Path

def get_logger(name: str = "mcp") -> logging.Logger:
    """
    MCP 서버용 로거 생성 (RotatingFileHandler 적용)
    
    - 로그 파일 위치: mcp/logs/
    - 최대 파일 크기: 5MB
    - 백업 파일: 3개
    """
    logger = logging.getLogger(name)

    # 기존 핸들러 제거 (중복 방지)
    if logger.hasHandlers():
        logger.handlers.clear()

    # 로그 디렉토리 생성 (mcp/logs)
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "mcp_server.log"

    # RotatingFileHandler 설정
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8"
    )

    # 로그 포맷
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%y/%m/%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)

    return logger

