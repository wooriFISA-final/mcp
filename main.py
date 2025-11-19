from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config.logger import get_logger
from server.mcp_server import (
    all_app,
    mcp_app)


logger = get_logger()

def create_app() -> FastAPI:
    # Root FastAPI에 REST와 MCP를 마운트
    root_app = FastAPI(
        title="Fisa MCP Server",
        description="FastMCP + HTTP Transport",
        lifespan= mcp_app.lifespan,
        version="0.1.0"
    )

    root_app.mount('/api',all_app)
    root_app.mount('/mcp',mcp_app)

    # 2. Add CORS middleware
    root_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    @root_app.get("/")
    async def root() -> dict:
        return {
            "message": "Stock Trading Service with MCP",
            "api": "/api",
            "mcp": "/mcp",
        }
    return root_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    logger.info("=" * 60)
    logger.info("Starting FastAPI + FastMCP Server (HTTP)")
    logger.info("=" * 60)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8888,
        reload=True,
        log_level="info"
    )