# FastMCP 객체 직접 import
from server.mcp_server import mcp  
from config.logger import get_logger

logger = get_logger()

if __name__ == "__main__":
    logger.info("Starting MCP Server...")
    # FastMCP 내부 run() 사용
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
        path="/",
        log_level="debug",
    )
