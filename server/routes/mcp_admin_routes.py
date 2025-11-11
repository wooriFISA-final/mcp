# server/routes/user_routes.py

from fastapi import APIRouter, HTTPException
from server.mcp_server import mcp

router = APIRouter(
    prefix="/mcp-admin",
    tags=["MCP 관리 API"]
)

# ======================
# ✅ 기본 헬스 및 정보 조회
# ======================
@router.get("/health")
async def health_check():
    """서버 헬스체크"""
    return {
        "status": "healthy",
        "service": mcp.name,
        "version": mcp.version,
        "transport": "streamable-http"
    }

@router.get("/info")
async def mcp_info():
    """MCP 서버 기본 정보"""
    tools = await mcp.get_tools()
    resources = await mcp.get_resources()
    prompts = await mcp.get_prompts()
    
    return {
        "name": mcp.name,
        "version": mcp.version,
        "transport": "streamable-http",
        "endpoint": "/mcp",
        "tools_count": len(tools),
        "resources_count": len(resources),
        "prompts_count": len(prompts),
    }

# ======================
# ✅ 등록된 툴/리소스/프롬프트 조회
# ======================
@router.get("/tools")
async def list_tools():
    """등록된 MCP Tool 목록"""
    try:
        tools_dict = await mcp.get_tools()  # dict[str, Tool] 반환
        
        return {
            "count": len(tools_dict),
            "tools": [
                {
                    "key": key,
                    "name": tool.name,
                    "description": tool.description or "No description",
                    "tags": list(tool.tags) if tool.tags else [],
                    "enabled": tool.enabled,
                }
                for key, tool in tools_dict.items()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool 목록 조회 실패: {str(e)}")

@router.get("/resources")
async def list_resources():
    """등록된 MCP Resource 목록"""
    try:
        resources_dict = await mcp.get_resources()  # dict[str, Resource] 반환
        
        return {
            "count": len(resources_dict),
            "resources": [
                {
                    "key": key,
                    "uri": resource.uri,
                    "name": resource.name or "Unnamed resource",
                    "description": resource.description or "No description",
                    "mime_type": resource.mime_type,
                    "tags": list(resource.tags) if resource.tags else [],
                }
                for key, resource in resources_dict.items()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Resource 목록 조회 실패: {str(e)}")

@router.get("/prompts")
async def list_prompts():
    """등록된 MCP Prompt 목록"""
    try:
        prompts_dict = await mcp.get_prompts()  # dict[str, Prompt] 반환
        
        return {
            "count": len(prompts_dict),
            "prompts": [
                {
                    "key": key,
                    "name": prompt.name,
                    "description": prompt.description or "No description",
                    "tags": list(prompt.tags) if prompt.tags else [],
                }
                for key, prompt in prompts_dict.items()
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prompt 목록 조회 실패: {str(e)}")

# ======================
# ✅ 특정 Tool/Resource/Prompt 상세 조회
# ======================
@router.get("/tools/{tool_key}")
async def get_tool_detail(tool_key: str):
    """특정 Tool의 상세 정보"""
    try:
        tool = await mcp.get_tool(tool_key)
        
        return {
            "key": tool.key,
            "name": tool.name,
            "description": tool.description,
            "tags": list(tool.tags) if tool.tags else [],
            "enabled": tool.enabled,
            "meta": tool.meta,
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_key}' 조회 실패: {str(e)}")

@router.get("/resources/{resource_key:path}")
async def get_resource_detail(resource_key: str):
    """특정 Resource의 상세 정보"""
    try:
        resource = await mcp.get_resource(resource_key)
        
        return {
            "key": resource.key,
            "uri": resource.uri,
            "name": resource.name,
            "description": resource.description,
            "mime_type": resource.mime_type,
            "tags": list(resource.tags) if resource.tags else [],
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Resource '{resource_key}' 조회 실패: {str(e)}")

@router.get("/prompts/{prompt_key}")
async def get_prompt_detail(prompt_key: str):
    """특정 Prompt의 상세 정보"""
    try:
        prompt = await mcp.get_prompt(prompt_key)
        
        return {
            "key": prompt.key,
            "name": prompt.name,
            "description": prompt.description,
            "tags": list(prompt.tags) if prompt.tags else [],
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Prompt '{prompt_key}' 조회 실패: {str(e)}")

# ======================
# ✅ 동적 Tool 관리
# ======================
@router.delete("/tools/{tool_name}")
async def unregister_tool(tool_name: str):
    """등록된 MCP Tool 해제"""
    try:
        mcp.remove_tool(tool_name)
        return {"status": "ok", "message": f"Tool '{tool_name}' 삭제 완료"}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Tool 삭제 실패: {str(e)}")

# ======================
# ✅ 디버깅용 엔드포인트
# ======================
@router.get("/debug/mcp")
async def debug_mcp():
    """MCP 객체 구조 확인 (개발용)"""
    tools = await mcp.get_tools()
    resources = await mcp.get_resources()
    prompts = await mcp.get_prompts()
    
    return {
        "type": str(type(mcp)),
        "name": mcp.name,
        "version": mcp.version,
        "tools_count": len(tools),
        "resources_count": len(resources),
        "prompts_count": len(prompts),
        "sample_tool_keys": list(tools.keys())[:3] if tools else [],
        "sample_resource_keys": list(resources.keys())[:3] if resources else [],
        "sample_prompt_keys": list(prompts.keys())[:3] if prompts else [],
    }