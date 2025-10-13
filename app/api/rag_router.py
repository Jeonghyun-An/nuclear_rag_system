# app/api/rag_router.py
"""
RAG 작업 전용 API 라우터 (자바단과 통신)
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from app.services.rag_orchestrator import RAGOrchestrator
from app.services.db_connector import DBConnector

router = APIRouter(prefix="/rag", tags=["rag"])

# ============ Request/Response 모델 ============

class ProcessRequest(BaseModel):
    """처리 요청"""
    data_ids: Optional[List[str]] = None  # 특정 문서만 처리 (없으면 전체)
    mode: str = "auto"  # auto | manual | failed
    limit: int = 10  # 한 번에 처리할 파일 수

class ProcessResponse(BaseModel):
    """처리 결과"""
    status: str
    mode: str
    processed: int
    success: int
    failed: int
    skipped: int

class StatusResponse(BaseModel):
    """문서 상태 응답"""
    data_id: str
    data_title: Optional[str]
    manual_edit: bool
    parse_completed: bool
    ocr_failed: bool
    rag_completed: bool
    ocr_pages: int
    in_milvus: bool
    parse_start: Optional[str]
    parse_end: Optional[str]

class StatsResponse(BaseModel):
    """전체 통계"""
    total: int
    completed: int
    pending_auto: int
    pending_manual: int
    pending_rag: int
    failed: int

# ============ 엔드포인트 ============

@router.post("/process", response_model=ProcessResponse)
async def process_documents(req: ProcessRequest, background_tasks: BackgroundTasks):
    """
    문서 처리 엔드포인트 (자바단에서 호출)
    
    Modes:
    - auto: 자동 OCR 대기 파일 처리
    - manual: 수기 편집 완료 파일 처리
    - failed: OCR 실패 파일 재시도
    """
    orch = RAGOrchestrator()
    
    # 특정 문서만 처리하는 경우
    if req.data_ids:
        results = {'success': 0, 'failed': 0, 'skipped': 0}
        for data_id in req.data_ids:
            try:
                if req.mode == "failed":
                    success = orch.retry_failed_file(data_id)
                else:
                    # 단일 문서 강제 처리
                    db = DBConnector()
                    file_meta = db.get_file_by_id(data_id)
                    if not file_meta:
                        results['failed'] += 1
                        continue
                    
                    if req.mode == "manual" or file_meta['manual_edit_yn'] == 'Y':
                        r = orch.process_manual_edit_files(limit=1)
                    else:
                        r = orch.process_auto_ocr_files(limit=1)
                    
                    results['success'] += r.get('success', 0)
                    results['failed'] += r.get('failed', 0)
            except Exception as e:
                print(f"❌ 처리 실패 [{data_id}]: {e}")
                results['failed'] += 1
        
        return ProcessResponse(
            status="completed",
            mode=req.mode,
            processed=results['success'] + results['failed'],
            success=results['success'],
            failed=results['failed'],
            skipped=results['skipped']
        )
    
    # 전체 처리
    if req.mode == "auto":
        results = orch.process_auto_ocr_files(limit=req.limit)
    elif req.mode == "manual":
        results = orch.process_manual_edit_files(limit=req.limit)
    else:
        raise HTTPException(400, f"Unknown mode: {req.mode}")
    
    return ProcessResponse(
        status="completed",
        mode=req.mode,
        processed=results.get('success', 0) + results.get('failed', 0),
        success=results.get('success', 0),
        failed=results.get('failed', 0),
        skipped=results.get('skipped', 0)
    )

@router.get("/status/{data_id}", response_model=StatusResponse)
def get_document_status(data_id: str):
    """문서 처리 상태 조회"""
    orch = RAGOrchestrator()
    status = orch.get_processing_status(data_id)
    
    if status.get('status') == 'not_found':
        raise HTTPException(404, f"Document not found: {data_id}")
    
    return StatusResponse(
        data_id=status['data_id'],
        data_title=status['data_title'],
        manual_edit=status['manual_edit'],
        parse_completed=status['parse_completed'],
        ocr_failed=status['ocr_failed'],
        rag_completed=status['rag_completed'],
        ocr_pages=status['ocr_pages'],
        in_milvus=status['in_milvus'],
        parse_start=str(status['parse_start']) if status['parse_start'] else None,
        parse_end=str(status['parse_end']) if status['parse_end'] else None
    )

@router.post("/retry/{data_id}")
def retry_ocr(data_id: str):
    """OCR 실패 파일 재시도"""
    orch = RAGOrchestrator()
    success = orch.retry_failed_file(data_id)
    
    if success:
        return {"status": "success", "message": f"재시도 완료: {data_id}"}
    else:
        raise HTTPException(500, f"재시도 실패: {data_id}")

@router.post("/reindex/{data_id}")
def reindex_document(data_id: str):
    """문서 재인덱싱 (Milvus 삭제 후 재처리)"""
    orch = RAGOrchestrator()
    success = orch.delete_and_reindex(data_id)
    
    if success:
        return {"status": "success", "message": f"재인덱싱 완료: {data_id}"}
    else:
        raise HTTPException(500, f"재인덱싱 실패: {data_id}")

@router.get("/stats", response_model=StatsResponse)
def get_statistics():
    """전체 통계 조회"""
    db = DBConnector()
    stats = db.get_statistics()
    
    return StatsResponse(
        total=stats['total'],
        completed=stats['completed'],
        pending_auto=stats['pending_auto'],
        pending_manual=stats['pending_manual'],
        pending_rag=stats['pending_rag'],
        failed=stats['failed']
    )

@router.get("/recent")
def get_recent_documents(limit: int = 20):
    """최근 처리 문서 조회"""
    db = DBConnector()
    
    query = """
    SELECT data_id, data_title, parse_yn, ocr_failed_yn, 
           manual_edit_yn, rag_completed_yn,
           parse_start_dt, parse_end_dt, reg_dt
    FROM file_metadata
    WHERE del_yn = 'N'
    ORDER BY reg_dt DESC
    LIMIT %s
    """
    
    with db.get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (limit,))
            return cursor.fetchall()

@router.get("/health")
def health_check():
    """헬스 체크"""
    db = DBConnector()
    db_ok = db.test_connection()
    
    if not db_ok:
        raise HTTPException(503, "DB 연결 실패")
    
    return {
        "status": "healthy",
        "db": "connected",
        "timestamp": str(datetime.now())
    }

from datetime import datetime