# mcp/test_rag_search.py

import os
import sys
from dotenv import load_dotenv, find_dotenv
from typing import List, Dict, Any

# 🎯 1. ENV 파일 로드: 현재 mcp 폴더에서 실행되더라도 루트 폴더의 .env를 찾습니다.
# sys.path를 조정하여 루트 경로를 포함시킵니다.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv(find_dotenv(usecwd=True, raise_error_if_not_found=False) or find_dotenv(usecwd=True) or find_dotenv("..")) 

# 🎯 2. RAG 라이브러리 임포트 (최신 및 안정 버전)
from langchain_huggingface import HuggingFaceEndpointEmbeddings 
from langchain_community.vectorstores import FAISS
# Note: Python 3.10 이상에서는 typing hints를 사용합니다.

# 3. RAG 설정 변수 로드
HF_EMBEDDING_MODEL = os.getenv("HF_EMBEDDING_MODEL", 'Qwen/Qwen3-Embedding-8B')
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", '../data/faiss_index')
HUGGINGFACEHUB_API_TOKEN = os.getenv("HUGGINGFACEHUB_API_TOKEN")


def _rag_similarity_search(query: str, k: int = 3) -> str:
    """FAISS DB를 로드하여 Hugging Face API를 통해 쿼리를 검색하고 결과를 텍스트로 반환합니다."""

    if not HUGGINGFACEHUB_API_TOKEN:
        return "🚨 RAG 검색 실패: HUGGINGFACEHUB_API_TOKEN이 설정되지 않았습니다."

    try:
        # 4. 임베딩 모델 로드 (HuggingFace API Endpoint 사용)
        embeddings = HuggingFaceEndpointEmbeddings(
            model=HF_EMBEDDING_MODEL,
            huggingfacehub_api_token=HUGGINGFACEHUB_API_TOKEN,
        )
        
        # 5. 벡터 저장소 로드 (Allow dangerous deserialization 필요)
        db = FAISS.load_local(VECTOR_DB_PATH, embeddings, allow_dangerous_deserialization=True)
        
        # 6. 유사도 검색 수행
        found_chunks = db.similarity_search(query, k=k)
        
        # 7. 결과 결합
        context = []
        for chunk in found_chunks:
            source = chunk.metadata.get("source", "출처 미상")
            context.append(f"[출처: {source}]\n{chunk.page_content}")

        return "\n---\n".join(context)
    
    except Exception as e:
        # 403 Forbidden 오류 등 네트워크/인증 오류를 포함하여 반환
        return f"🚨 RAG 검색 시스템 오류: {type(e).__name__} - {e}"

if __name__ == "__main__":
    # 🎯 검색 쿼리: 정책 변동의 핵심 키워드를 사용합니다.
    search_query = "대출 LTV 비율 변경 사항과 시행일 정보를 찾아줘" 
    
    print(f"\n--- 🔍 RAG 검색 시작 (쿼리: '{search_query}') ---")
    
    search_result = _rag_similarity_search(search_query, k=2)
    
    print(search_result)
    print("\n--- ✅ RAG 실습 완료 ---\n")