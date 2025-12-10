# 🔌 WooriZip MCP Server - Model Context Protocol Server

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-green?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/FastMCP-Latest-orange?logo=fastapi&logoColor=white" alt="FastMCP">
  <img src="https://img.shields.io/badge/FAISS-Vector_Search-red?logo=meta&logoColor=white" alt="FAISS">
  <img src="https://img.shields.io/badge/MySQL-8.0+-blue?logo=mysql&logoColor=white" alt="MySQL">
</p>

<p align="center">
  FastAPI + FastMCP 기반의 MCP(Model Context Protocol) 서버로<br/>
  <strong>LLM 에이전트가 활용할 수 있는 도구(Tool)와 리소스(Resource)</strong>를 제공합니다.
</p>

---

### 📊 API 문서 (Swagger)
<!-- Swagger UI 스크린샷 -->
<img width="1458" height="1110" alt="Image" src="https://github.com/user-attachments/assets/957bd8e5-4b94-4cf0-83d8-2e90d6bdcc57" />

<img width="1448" height="648" alt="Image" src="https://github.com/user-attachments/assets/b282ab3c-07d7-445d-837c-d011addfd55f" />

---

## 📋 Table of Contents

- [Features](#-features)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Configuration](#%EF%B8%8F-configuration)
- [API Reference](#-api-reference)
- [MCP Tools](#-mcp-tools)
- [Project Structure](#-project-structure)
- [Docker Deployment](#-docker-deployment)

---

## ✨ Features

### 🎯 주요 기능
- **MCP Tool 자동 변환** - FastAPI 엔드포인트를 MCP Tool로 자동 노출
- **JSON-RPC 2.0** - 표준 MCP 프로토콜 통신 지원
- **RAG 검색** - FAISS 기반 금융 상품 벡터 검색
- **관리자 API** - 서버 상태 및 Tool 모니터링
- **데이터베이스 연동** - MySQL RDS 연결

### 🔧 MCP Components
- 🛠️ **Tools** - 재무 계획, 리포트 생성 도구
- 📚 **Resources** - 데이터베이스 리소스
- 📝 **Prompts** - LLM 프롬프트 템플릿

### 🗃️ RAG (Retrieval-Augmented Generation)
- 💰 **예금 상품 검색** - FAISS 벡터 인덱스
- 💵 **적금 상품 검색** - FAISS 벡터 인덱스
- 📋 **정책 문서 검색** - 주택청약 정책 RAG

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- MySQL 8.0+ (또는 AWS RDS)
- uv (권장) 또는 pip

### 30초 시작하기

```bash
# 1. 저장소 클론
git clone https://github.com/your-org/woorizip-mcp.git
cd mcp

# 2. 환경 변수 설정
cp .env.example .env
# .env 파일에서 DB 연결 정보 등 설정

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 서버 실행
python -m uvicorn main:app --host 0.0.0.0 --port 8888 --reload
```

서버가 시작되면:
- 📖 REST API 문서: `http://localhost:8888/docs`
- 🔌 MCP 엔드포인트: `http://localhost:8888/mcp`

---

## ⚙️ Configuration

### 환경 변수 (.env)

```bash
# ============================================
# MySQL Database Configuration
# ============================================
DB_HOST=your-rds-endpoint.amazonaws.com
DB_PORT=3306
DB_USER=admin
DB_PASSWORD=your_password
DB_NAME=woorizip

# ============================================
# AI/ML Configuration
# ============================================
HF_TOKEN=your_huggingface_token
EMBED_MODEL=Qwen/Qwen3-Embedding-8B
PLAN_LLM=qwen3:8b
EMBEDDING_API_URL=http://gpu-server:port/embed

# ============================================
# CORS Configuration
# ============================================
CORS_ORIGINS=http://localhost:3000,https://woorizip.info

# ============================================
# Server Configuration
# ============================================
# MCP Server Port: 8888
```

### 환경별 설정

| 환경 | DB_HOST | EMBEDDING_API_URL | Port |
|------|---------|-------------------|------|
| **개발** | `localhost` | `http://localhost:11434/embed` | 8888 |
| **프로덕션** | RDS Endpoint | GPU Server Private IP | 8888 |

---

## 📖 API Reference

### MCP 엔드포인트

| Method | Path | Protocol | Description |
|--------|------|----------|-------------|
| `POST` | `/mcp` | JSON-RPC 2.0 | MCP 통신 엔드포인트 |

#### MCP 호출 예시

```bash
# Tool 목록 조회
curl -X POST http://localhost:8888/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### REST API 엔드포인트

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | 서버 정보 |
| `GET` | `/api/mcp_admin/health` | 헬스체크 |
| `GET` | `/api/mcp_admin/info` | MCP 서버 정보 |
| `GET` | `/api/mcp_admin/tools` | 등록된 Tool 목록 |
| `GET` | `/api/mcp_admin/tools/{tool_key}` | Tool 상세 정보 |
| `GET` | `/api/mcp_admin/resources` | Resource 목록 |
| `GET` | `/api/mcp_admin/prompts` | Prompt 목록 |
| `POST` | `/api/mcp_admin/jsonrpc` | JSON-RPC 테스트 |

---

## 🛠 MCP Tools

### Plan Agent Tools
재무 계획 수립을 위한 도구들

| Tool Name | Description |
|-----------|-------------|
| `tools_input/parse_currency` | 한국어 금액 단위를 정수로 변환 (예: '3억 5천만' → 350000000) |
| `tools_input/validate_input_data` | 주택 구매 계획 입력 검증 |
| `tools_plan/search_deposit_products` | 예금 상품 검색 (RAG) |
| `tools_plan/search_saving_products` | 적금 상품 검색 (RAG) |
| `tools_plan/calculate_savings` | 저축 시뮬레이션 |

### Report Agent Tools
리포트 생성을 위한 도구들

| Tool Name | Description |
|-----------|-------------|
| `tools_report/get_user_plan` | 사용자 재무 계획 조회 |
| `tools_report/search_policy` | 주택청약 정책 검색 (RAG) |
| `tools_report/generate_report` | 종합 리포트 생성 |

---

## 📁 Project Structure

```
mcp/
├── main.py                     # 🚀 서버 엔트리포인트
├── mcp.json                    # MCP 설정 파일
│
├── config/                     # ⚙️ 설정
│   └── logger.py               # 로깅 설정
│
├── server/                     # 🔌 서버 모듈
│   ├── mcp_server.py           # FastMCP 서버 설정
│   │
│   ├── api/                    # API 모듈
│   │   ├── mcp_admin_routes.py # 관리자 API
│   │   ├── tools/              # MCP Tools
│   │   │   ├── plan_agent_tools.py   # 재무 계획 도구
│   │   │   └── report_agent_tools.py # 리포트 도구
│   │   └── resources/          # MCP Resources
│   │       └── db_tools.py     # DB 리소스
│   │
│   ├── routes/                 # 라우터
│   │   ├── mcp_route.py        # MCP Tool 라우터
│   │   └── data_route.py       # 데이터 라우터
│   │
│   ├── rag/                    # 🔍 RAG 모듈
│   │   ├── faiss_deposit_products/ # 예금 FAISS 인덱스
│   │   ├── faiss_saving_products/  # 적금 FAISS 인덱스
│   │   └── faiss_report_policy/    # 정책 FAISS 인덱스
│   │
│   ├── data/                   # 📚 데이터
│   │   └── policy_documents/   # 정책 문서
│   │
│   ├── core/                   # 핵심 기능
│   │   └── config.py           # 설정 관리
│   │
│   └── schemas/                # Pydantic 스키마
│
├── logs/                       # 📝 로그
│   └── mcp_server.log
│
├── Dockerfile                  # Docker 빌드
├── requirements.txt            # 의존성
├── pyproject.toml              # 프로젝트 설정
└── .env.example                # 환경 변수 템플릿
```

---

## 🐳 Docker Deployment

### 빠른 배포

```bash
# 환경 변수 설정
cp .env.example .env
# .env 파일 수정

# Docker 이미지 빌드
docker build -t woorizip-mcp:latest .

# 컨테이너 실행
docker run -d \
  --name mcp \
  -p 8888:8888 \
  --env-file .env \
  woorizip-mcp:latest
```

### 로그 확인

```bash
# Docker 로그
docker logs -f mcp

# 애플리케이션 로그
docker exec mcp cat logs/mcp_server.log
```

---

## 📊 Logging

- **로그 파일**: `logs/mcp_server.log`
- **로테이션**: 5MB 크기로 로테이션
- **백업**: 최대 3개 백업 파일 유지
- **설정**: `config/logger.py`

---

## 🔒 Security

- ✅ 환경 변수로 민감한 정보 관리
- ✅ CORS 설정으로 허용된 도메인만 접근
- ✅ JSON-RPC 표준 프로토콜 사용
- ✅ 로깅을 통한 요청 추적

---

<p align="center">
  Made by WooriFisa Team 6
</p>
