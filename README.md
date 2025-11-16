# FISA-MCP 서버 기술 문서

## 1. 개요

이 문서는 FastAPI와 FastMCP를 사용하여 구축된 MCP(Model-facing Copilot Protocol) 서버의 기술적인 내용을 설명합니다. 이 서버는 다양한 백엔드 기능을 LLM(Large Language Model)이 활용할 수 있는 표준화된 도구(Tool)로 노출하는 역할을 합니다.

주요 기능은 다음과 같습니다.
- FastAPI로 구현된 REST API 엔드포인트를 MCP Tool로 자동 변환
- JSON-RPC 2.0 프로토콜을 통한 MCP 통신 지원
- 서버 상태 및 등록된 도구를 모니터링할 수 있는 관리자용 REST API 제공

## 2. 아키텍처

본 서버는 FastAPI를 기반으로 구축되었으며, `FastMCP.from_fastapi`를 통해 기존의 REST API 엔드포인트들을 MCP Tool로 자동 변환합니다.

- **진입점**: `main.py`는 uvicorn을 통해 FastAPI 애플리케이션을 실행하는 진입점입니다. `root_app` 인스턴스를 생성하고, `/api` 경로에는 REST API를, `/mcp` 경로에는 MCP 애플리케이션을 마운트합니다.
- **애플리케이션 분리**:
    - `tools_app`: MCP Tool로 변환될 API들만 포함하는 FastAPI 인스턴스입니다. (`mcp_route`, `data_route` 포함)
    - `all_app`: `tools_app`의 원본 API와 관리자용 API를 포함하는 전체 REST API 서버입니다. 이 앱은 `root_app`의 `/api` 경로에 마운트됩니다.
    - `mcp_app`: `tools_app`을 기반으로 생성된 순수 MCP JSON-RPC 애플리케이션입니다. 이 앱은 `root_app`의 `/mcp` 경로에 마운트됩니다.
- **MCP 서버 설정**: `server/mcp_server.py`에서 `FastMCP` 객체를 생성하고, `tools_app`을 래핑하여 MCP 서버를 구성합니다.
- **라우팅**:
    - `server/routes/mcp_route.py`: MCP Tool로 노출될 API 라우터들을 `/tools` 접두사로 그룹화합니다. (`user_tools`, `report_tools`, `plan_tools` 포함)
    - `server/routes/data_route.py`: 데이터베이스 관련 리소스 Tool을 포함하는 라우터입니다.
    - `server/api/tools/`: `user_tools.py`, `report_tools.py`, `plan_tools.py`와 같이 실제 비즈니스 로직을 담은 개별 Tool 파일들이 위치합니다.
    - `server/api/mcp_admin_routes.py`: MCP 서버 관리를 위한 REST API 라우터를 `/mcp_admin` 접두사로 정의합니다.

## 3. API 엔드포인트

### 3.1. MCP 엔드포인트

- **Endpoint**: `http://{host}:{port}/mcp`
- **Method**: `POST`
- **Protocol**: JSON-RPC 2.0

LLM 에이전트 또는 MCP 클라이언트가 통신하는 기본 엔드포인트입니다.

**예시: `tools/list` 호출**
```bash
curl -L -X POST http://localhost:8888/mcp/ \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### 3.2. 관리자 및 REST API

- **Prefix**: `/api`

서버의 상태를 모니터링하고 원본 REST API를 직접 호출할 수 있습니다.

| Method | Path                               | 설명                                     | 
|--------|------------------------------------|------------------------------------------|
| `GET`  | `/`                                | 루트 엔드포인트. 서버 메시지를 반환합니다. |
| `GET`  | `/mcp_admin/health`                | 서버의 상태를 확인합니다.                |
| `GET`  | `/mcp_admin/info`                  | MCP 서버의 기본 정보를 조회합니다.       |
| `POST` | `/mcp_admin/jsonrpc`               | MCP JSON-RPC 요청을 테스트하는 프록시 엔드포인트입니다.  |
| `GET`  | `/mcp_admin/tools`                 | 등록된 모든 MCP Tool 목록을 조회합니다.  |
| `GET`  | `/mcp_admin/tools/{tool_key}`      | 특정 Tool의 상세 정보를 조회합니다.      |
| `GET`  | `/mcp_admin/resources`             | 등록된 모든 MCP Resource 목록을 조회합니다.|
| `GET`  | `/mcp_admin/prompts`               | 등록된 모든 MCP Prompt 목록을 조회합니다.  |
| `GET`  | `/tools/users/get_user`            | REST API로 특정 유저를 조회합니다.       | 

## 4. 등록된 MCP Tools

라우터의 접두사 설정에 따라, API 경로는 자동으로 MCP Tool의 `name`으로 변환됩니다. 주요 Tool은 다음과 같습니다.

- **`tools_users/create_user`**: 사용자의 이름과 나이를 받아 새로운 사용자를 생성합니다.
  - **API Path**: `POST /api/tools/users/create_user`

- **`tools_users/get_user`**: 사용자의 이름을 받아 등록된 사용자를 조회합니다.
  - **API Path**: `GET /api/tools/users/get_user`

- **`tools_input/parse_currency`**: 한국어 금액 단위를 정수로 변환합니다. (예: '3억 5천만' → 350000000)
  - **API Path**: `POST /api/tools/input/parse_currency`

- **`tools_input/validate_input_data`**: 주택 구매 계획과 관련된 사용자 입력을 검증하고 정규화합니다.
  - **API Path**: `POST /api/tools/input/validate_input_data`

- **(기타)**: `report_tools`, `db_tools` 등 다양한 Tool이 등록되어 있습니다. 전체 목록은 `GET /api/mcp_admin/tools`를 통해 확인할 수 있습니다.

## 5. 실행 방법

### 5.1. 개발 환경에서 실행

프로젝트 루트 디렉토리에서 아래 명령어를 실행하여 uvicorn 개발 서버를 시작합니다.

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8888 --reload
```

서버가 시작되면 `http://localhost:8888/docs`에서 REST API 문서를, `http://localhost:8888/mcp`에서 MCP 엔드포인트를 확인할 수 있습니다.

### 5.2. 로깅

- 로그 파일은 `mcp/logs/mcp_server.log`에 저장됩니다.
- 로그 파일은 5MB 크기로 로테이션되며, 최대 3개의 백업 파일을 유지합니다.
- 로거 설정은 `config/logger.py`에서 확인할 수 있습니다.

```python
mcp/
├── __init__.py
├── .env.sample
├── .gitignore
├── main.py
├── mcp.json
├── README.md
├── config/
│   ├── __pycache__/
│   └── logger.py
├── logs/
│   └── mcp_server.log
└── server/
    ├── __init__.py
    ├── mcp_server.py
    ├── api/
    │   ├── __pycache__/
    │   ├── mcp_admin_routes.py
    │   ├── prompts/
    │   │   ├── __pycache__/
    │   │   └── user_prompts.py
    │   ├── resources/
    │   │   ├── __pycache__/
    │   │   ├── db_tools.py
    │   │   └── user_resources.py
    │   └── tools/
    │       ├── __pycache__/
    │       ├── plan_tools.py
    │       ├── report_tools.py
    │       └── user_tools.py
    ├── core/
    │   └── config.py
    ├── routes/
    │   ├── __pycache__/
    │   ├── data_route.py
    │   └── mcp_route.py
    └── schemas/
```