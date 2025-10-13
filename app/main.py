# app/main.py
"""
FastAPI 메인 앱 - DB 기반 RAG 시스템
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import os

# 라우터 임포트
from app.api.rag_router import router as rag_router        # 🆕 DB 기반 RAG
from app.api.chat_router import router as chat_router      # 🆕 챗봇 기능
from app.api.llama_router import router as llama_router    # ⚠️ 레거시 (MinIO 기반)

# 백그라운드 폴링 태스크
async def polling_task():
    """
    DB 폴링하여 자동 RAG 처리
    - 자동 OCR 파일 처리
    - 수기 편집 완료 파일 처리
    """
    from app.services.rag_orchestrator import RAGOrchestrator
    
    if os.getenv("RAG_AUTO_PROCESS", "true").lower() != "true":
        print("⚠️ RAG 자동 처리 비활성화 (RAG_AUTO_PROCESS=false)")
        return
    
    interval = int(os.getenv("RAG_POLLING_INTERVAL", 60))
    batch_size = int(os.getenv("RAG_BATCH_SIZE", 10))
    
    print(f"🔄 RAG 폴링 시작 (interval={interval}s, batch={batch_size})")
    
    try:
        orch = RAGOrchestrator()
    except Exception as e:
        print(f"❌ RAG Orchestrator 초기화 실패: {e}")
        print("⚠️ DB 연결 문제일 수 있습니다. 레거시 모드로 계속 실행합니다.")
        return
    
    while True:
        try:
            # 1. 자동 OCR 파일 처리
            auto_results = orch.process_auto_ocr_files(limit=batch_size)
            if auto_results['success'] > 0 or auto_results['failed'] > 0:
                print(f"\n📊 자동 OCR 결과:")
                print(f"  ✅ 성공: {auto_results['success']}")
                print(f"  ❌ 실패: {auto_results['failed']}")
                print(f"  ⏭️ 스킵: {auto_results['skipped']}")
            
            # 2. 수기 편집 파일 처리
            manual_results = orch.process_manual_edit_files(limit=batch_size)
            if manual_results['success'] > 0 or manual_results['failed'] > 0:
                print(f"\n📊 수기 편집 결과:")
                print(f"  ✅ 성공: {manual_results['success']}")
                print(f"  ❌ 실패: {manual_results['failed']}")
                print(f"  ⏭️ 스킵: {manual_results['skipped']}")
            
        except Exception as e:
            print(f"\n❌ 폴링 에러: {e}")
            import traceback
            traceback.print_exc()
        
        # 다음 폴링까지 대기
        await asyncio.sleep(interval)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    앱 시작/종료 시 실행
    - 시작: 백그라운드 폴링 태스크 시작
    - 종료: 태스크 정리
    """
    # 시작 시
    print("\n" + "="*60)
    print("🚀 RAG API 서버 시작")
    print("="*60)
    
    # DB 연결 테스트 (선택적)
    try:
        from app.services.db_connector import DBConnector
        db = DBConnector()
        if db.test_connection():
            print("✅ DB 연결 성공 - DB 기반 RAG 활성화")
        else:
            print("⚠️ DB 연결 실패 - 레거시 MinIO 모드로 동작")
    except Exception as e:
        print(f"⚠️ DB 연결 테스트 실패: {e}")
        print("⚠️ 레거시 MinIO 모드로 동작")
    
    # 백그라운드 태스크 시작
    task = asyncio.create_task(polling_task())
    
    yield
    
    # 종료 시
    print("\n" + "="*60)
    print("🛑 RAG API 서버 종료")
    print("="*60)
    
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("✅ 백그라운드 태스크 종료")

# FastAPI 앱 생성
API_BASE = "/llama"

app = FastAPI(
    title="Nuclear RAG API",
    description="원자력 문서 RAG 시스템 - DB 연동 + 레거시 호환",
    version="2.0.0",
    docs_url=f"{API_BASE}/docs",
    openapi_url=f"{API_BASE}/openapi.json",
    redoc_url=None,
    lifespan=lifespan
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록 (우선순위 중요!)
app.include_router(rag_router, prefix=API_BASE)      # /llama/rag/*
app.include_router(chat_router, prefix=API_BASE)     # /llama/chat/*
app.include_router(llama_router, prefix=API_BASE)    # /llama/* (레거시)

# 헬스 체크
@app.get(f"{API_BASE}/healthz")
def healthz():
    """헬스 체크 엔드포인트"""
    return {
        "status": "ok",
        "service": "rag-api",
        "version": "2.0.0"
    }

@app.get("/")
def root():
    """루트 엔드포인트"""
    return {
        "message": "Nuclear RAG API",
        "docs": f"{API_BASE}/docs",
        "health": f"{API_BASE}/healthz",
        "endpoints": {
            "rag": f"{API_BASE}/rag/*",
            "chat": f"{API_BASE}/chat/*",
            "legacy": f"{API_BASE}/*"
        }
    }

# 디버깅용: 환경변수 확인
@app.get(f"{API_BASE}/config")
def get_config():
    """환경변수 확인 (개발 환경 전용)"""
    return {
        "mode": "db" if os.getenv("DB_HOST") else "minio-only",
        "db_host": os.getenv("DB_HOST"),
        "db_port": os.getenv("DB_PORT"),
        "db_name": os.getenv("DB_NAME"),
        "milvus_host": os.getenv("MILVUS_HOST"),
        "milvus_port": os.getenv("MILVUS_PORT"),
        "rag_auto_process": os.getenv("RAG_AUTO_PROCESS"),
        "rag_polling_interval": os.getenv("RAG_POLLING_INTERVAL"),
        "rag_batch_size": os.getenv("RAG_BATCH_SIZE"),
    }# app/main.py
"""
FastAPI 메인 앱 - DB 기반 RAG 시스템
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import os

from app.api.rag_router import router as rag_router
# 기존 llama_router는 챗봇 기능만 남기고 chat_router로 분리 가능
# from app.api.chat_router import router as chat_router

# 백그라운드 폴링 태스크
async def polling_task():
    """
    DB 폴링하여 자동 RAG 처리
    - 자동 OCR 파일 처리
    - 수기 편집 완료 파일 처리
    """
    from app.services.rag_orchestrator import RAGOrchestrator
    
    if os.getenv("RAG_AUTO_PROCESS", "true").lower() != "true":
        print("⚠️ RAG 자동 처리 비활성화 (RAG_AUTO_PROCESS=false)")
        return
    
    interval = int(os.getenv("RAG_POLLING_INTERVAL", 60))
    batch_size = int(os.getenv("RAG_BATCH_SIZE", 10))
    
    print(f"🔄 RAG 폴링 시작 (interval={interval}s, batch={batch_size})")
    
    orch = RAGOrchestrator()
    
    while True:
        try:
            # 1. 자동 OCR 파일 처리
            auto_results = orch.process_auto_ocr_files(limit=batch_size)
            if auto_results['success'] > 0 or auto_results['failed'] > 0:
                print(f"\n📊 자동 OCR 결과:")
                print(f"  ✅ 성공: {auto_results['success']}")
                print(f"  ❌ 실패: {auto_results['failed']}")
                print(f"  ⏭️ 스킵: {auto_results['skipped']}")
            
            # 2. 수기 편집 파일 처리
            manual_results = orch.process_manual_edit_files(limit=batch_size)
            if manual_results['success'] > 0 or manual_results['failed'] > 0:
                print(f"\n📊 수기 편집 결과:")
                print(f"  ✅ 성공: {manual_results['success']}")
                print(f"  ❌ 실패: {manual_results['failed']}")
                print(f"  ⏭️ 스킵: {manual_results['skipped']}")
            
        except Exception as e:
            print(f"\n❌ 폴링 에러: {e}")
            import traceback
            traceback.print_exc()
        
        # 다음 폴링까지 대기
        await asyncio.sleep(interval)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    앱 시작/종료 시 실행
    - 시작: 백그라운드 폴링 태스크 시작
    - 종료: 태스크 정리
    """
    # 시작 시
    print("\n" + "="*60)
    print("🚀 RAG API 서버 시작")
    print("="*60)
    
    # DB 연결 테스트
    from app.services.db_connector import DBConnector
    db = DBConnector()
    if db.test_connection():
        print("✅ DB 연결 성공")
    else:
        print("⚠️ DB 연결 실패 - 수동 처리 모드로 전환")
    
    # 백그라운드 태스크 시작
    task = asyncio.create_task(polling_task())
    
    yield
    
    # 종료 시
    print("\n" + "="*60)
    print("🛑 RAG API 서버 종료")
    print("="*60)
    
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("✅ 백그라운드 태스크 종료")

# FastAPI 앱 생성
API_BASE = "/llama"

app = FastAPI(
    title="Nuclear RAG API",
    description="원자력 문서 RAG 시스템 - 자바 DB 연동",
    version="2.0.0",
    docs_url=f"{API_BASE}/docs",
    openapi_url=f"{API_BASE}/openapi.json",
    redoc_url=None,
    lifespan=lifespan
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(rag_router, prefix=API_BASE)
# app.include_router(chat_router, prefix=API_BASE)  # 챗봇 기능 분리 시

# 헬스 체크
@app.get(f"{API_BASE}/healthz")
def healthz():
    """헬스 체크 엔드포인트"""
    return {
        "status": "ok",
        "service": "rag-api",
        "version": "2.0.0"
    }

@app.get("/")
def root():
    """루트 엔드포인트"""
    return {
        "message": "Nuclear RAG API",
        "docs": f"{API_BASE}/docs",
        "health": f"{API_BASE}/healthz"
    }

# 디버깅용: 환경변수 확인
@app.get(f"{API_BASE}/config")
def get_config():
    """환경변수 확인 (개발 환경 전용)"""
    return {
        "db_host": os.getenv("DB_HOST"),
        "db_port": os.getenv("DB_PORT"),
        "db_name": os.getenv("DB_NAME"),
        "milvus_host": os.getenv("MILVUS_HOST"),
        "milvus_port": os.getenv("MILVUS_PORT"),
        "rag_auto_process": os.getenv("RAG_AUTO_PROCESS"),
        "rag_polling_interval": os.getenv("RAG_POLLING_INTERVAL"),
        "rag_batch_size": os.getenv("RAG_BATCH_SIZE"),
    }