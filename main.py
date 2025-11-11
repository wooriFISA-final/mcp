# ============================================
# main.py (FastAPI + FastMCP Streamable-HTTP)
# ============================================
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config.logger import get_logger
from server.mcp_server import mcp
from server.routes.mcp_admin_routes import router as mcp_admin_router
import uvicorn

logger = get_logger()

# FastAPI 애플리케이션 생성
app = FastAPI(
    title="Fisa MCP Server",
    description="FastMCP + Streamable-HTTP Transport",
    version="0.1.0"
)

# CORS 미들웨어 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mcp_admin_router)

app.mount("/mcp/", mcp.http_app())


@app.get("/")
async def root():
    return {"status": "ok", "message": "AI Agent API is running 🚀"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Starting FastAPI + FastMCP Server (Streamable-HTTP)")
    logger.info("=" * 60)
    logger.info("🚀 MCP Endpoint: http://0.0.0.0:8000/mcp")
    logger.info("💚 Health Check: http://0.0.0.0:8000/health")
    logger.info("ℹ️  Server Info: http://0.0.0.0:8000/info")
    logger.info("🔧 REST API: http://0.0.0.0:8000/api/users")
    logger.info("=" * 60)
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )