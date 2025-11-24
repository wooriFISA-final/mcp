import os
import glob
from dotenv import load_dotenv, find_dotenv, dotenv_values
from pathlib import Path
import time
import re 

# 🎯 2. 라이브러리 임포트 
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter 
from langchain_huggingface import HuggingFaceEndpointEmbeddings 
from langchain_community.vectorstores import FAISS

# 🎯 1. ENV 파일 로드 및 값 추출
load_dotenv(find_dotenv(usecwd=True, raise_error_if_not_found=False) or find_dotenv(usecwd=True) or find_dotenv("..")) 
# 셸 환경 변수 충돌 방지를 위해 dotenv_values로 직접 파일을 읽어옵니다.
ENV_VALUES = dotenv_values(find_dotenv(usecwd=True, raise_error_if_not_found=False) or find_dotenv(usecwd=True) or find_dotenv(".."))

# 🎯 3. 환경 변수 설정
# ENV_VALUES 딕셔너리에서 값을 가져오고, 기본값은 Qwen으로 설정합니다.
HF_EMBEDDING_MODEL = os.getenv("HF_EMBEDDING_MODEL", ENV_VALUES.get("HF_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"))

VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", ENV_VALUES.get("VECTOR_DB_PATH", '../data/faiss_index'))
POLICY_DIR = "../data/policy_documents"
HUGGINGFACEHUB_API_TOKEN = os.getenv("HUGGINGFACEHUB_API_TOKEN", ENV_VALUES.get("HUGGINGFACEHUB_API_TOKEN")) 


def build_db():
    """PDF 파일을 로드, 분할, 임베딩하여 FAISS 벡터 DB를 구축합니다."""
    
    print(f"--- RAG 벡터 데이터베이스 구축 시작 (Model: {HF_EMBEDDING_MODEL}) ---")
    
    # 1. PDF 파일 경로 확인 및 로드
    if not os.path.exists(POLICY_DIR):
        print(f"❌ '{POLICY_DIR}' 폴더가 존재하지 않습니다. data/policy_documents 폴더를 확인해주세요.")
        return

    file_paths = glob.glob(os.path.join(POLICY_DIR, '*.pdf'))
    if not file_paths:
        print(f"✅ policy_documents 폴더에 PDF 파일이 없습니다. 문서를 추가해 주세요.")
        return

    documents = []
    for file_path in file_paths:
        loader = PyPDFLoader(file_path)
        documents.extend(loader.load())

    # 2. 텍스트 분할 (Split) - [정책 구조 기반 최적화]
    
    # 🎯 [최종 강화된 구분자]: 정책 조항 하나하나를 독립된 청크로 분리합니다.
    custom_separators = [
        # 1. 최우선 분리: 정책 장(章)과 조(條)의 시작
        r"\n제[0-9]{1,3}장\s",           # 예: "\n제1장 "
        r"\n제[0-9]{1,3}조\s",           # 예: "\n제3조 "
        
        # 2. 호(號) 또는 목(目)의 시작 (정책 조항의 가장 작은 단위)
        # 이 구분자는 띄어쓰기 유무에 관계없이 조항 시작 기호 앞에서 강제 분리합니다.
        r"\n[가-힣\d]\.\s?",             # 예: \n가., \n1. (뒤에 공백이 있어도/없어도 분리)
        r"\n\([가-힣\d]{1,2}\)\s?",      # 예: \n(1), \n(가), \n(2) (뒤에 공백이 있어도/없어도 분리)
        
        # 3. 개행 문자
        r"\n",                           
        
        # 4. 일반적인 공백
        " ",
        ""
    ]
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,  
        chunk_overlap=50, 
        separators=custom_separators,
        keep_separator=True
    )
    texts = text_splitter.split_documents(documents)
    print(f"➡️ 총 {len(documents)}개 문서에서 {len(texts)}개의 텍스트 청크 생성 완료.")
    
    # 3. 임베딩 모델 로드 (HuggingFace Endpoint API 사용)
    print(f"🤖 임베딩 모델 로드 및 Hugging Face API 연결 중... (사용 모델: {HF_EMBEDDING_MODEL})")
    
    if not HUGGINGFACEHUB_API_TOKEN:
        print("❌ HUGGINGFACEHUB_API_TOKEN 환경 변수가 설정되지 않았습니다. .env 파일을 확인해 주세요.")
        return
    
    # 🎯 HuggingFaceEndpointEmbeddings 사용
    embeddings = HuggingFaceEndpointEmbeddings(
        model=HF_EMBEDDING_MODEL, 
        huggingfacehub_api_token=HUGGINGFACEHUB_API_TOKEN,
    )
    
    # 4. 벡터 DB 생성 및 저장
    print(f"💾 FAISS 벡터 DB 생성 중... ({len(texts)}개 청크)")
    
    # 🎯 API 요청 안정화: 청크를 배치(Batch)로 처리하고 지연 시간을 추가합니다.
    batch_size = 32  # 한 번에 처리할 청크 개수
    sleep_time = 3   # 배치 처리 후 대기 시간 (초)
    
    if not texts:
        print("경고: 분할된 텍스트 청크가 없습니다. DB 구축을 건너뜁니다.")
        return
        
    # 빈 FAISS DB 초기화 (첫 번째 배치를 기준으로 생성)
    first_batch = texts[:batch_size]
    remaining_texts = texts[batch_size:]

    print(f"   [Step 1/2] 첫 {len(first_batch)}개 청크 처리 중...")
    try:
        # DB 초기 생성
        db = FAISS.from_documents(first_batch, embeddings)
    except Exception as e:
        print(f"🚨 DB 초기 생성 실패: {e}")
        return

    print(f"   [Step 2/2] 나머지 {len(remaining_texts)}개 청크 배치 처리 및 대기 중...")
    
    # 나머지 청크를 배치 처리
    total_processed = len(first_batch)
    
    for i in range(0, len(remaining_texts), batch_size):
        batch = remaining_texts[i:i + batch_size]
        
        # 429 오류 방지를 위해 명시적으로 대기
        print(f"   -- API 요청 지연 ({sleep_time}초 대기) --")
        time.sleep(sleep_time)
        
        try:
            # DB에 추가
            db.add_documents(batch)
            total_processed += len(batch)
            print(f"   -> {total_processed} / {len(texts)}개 청크 추가 완료.")
        except Exception as e:
            print(f"🚨 임베딩 오류 발생: {e}. DB 구축을 중단합니다.")
            return

    # 벡터 DB 경로 생성 및 저장
    Path(VECTOR_DB_PATH).mkdir(parents=True, exist_ok=True)
    db.save_local(VECTOR_DB_PATH) 

    print(f"--- ✅ 벡터 DB 구축 완료 ---")
    print(f"저장 경로: {VECTOR_DB_PATH}")

if __name__ == "__main__":
    # 🚨 주의: 이 스크립트를 실행하기 전에 서버가 실행 중이 아닌지 확인하세요.
    build_db()