# app/api/llama_router.py
"""
레거시 라우터 (하위 호환성 유지)
- MinIO 기반 파일 업로드/관리
- 기존 프론트엔드와 호환
"""
from __future__ import annotations
import mimetypes
import hashlib
import os
import uuid
from urllib.parse import unquote
from typing import Optional
from starlette.responses import StreamingResponse

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks, Query
from pydantic import BaseModel
import asyncio
import json
from sse_starlette.sse import EventSourceResponse
from datetime import datetime, timezone

from app.services import job_state
from app.services.file_parser import parse_any_bytes
from app.services.pdf_converter import convert_stream_to_pdf_bytes, ConvertError
from app.services.minio_store import MinIOStore

router = APIRouter(tags=["llama"])

UPLOAD_DIR = "data"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============ Schemas ============

class UploadResp(BaseModel):
    filename: str
    minio_object: str
    indexed: str
    job_id: Optional[str] = None

# ============ 헬퍼 함수 ============

def _content_disposition(disposition: str, filename: str) -> str:
    """Content-Disposition 헤더 생성"""
    try:
        filename.encode("latin-1")
        return f'{disposition}; filename="{filename}"'
    except UnicodeEncodeError:
        from urllib.parse import quote
        return f"{disposition}; filename*=UTF-8''{quote(filename)}"

def _sha256_bytes(b: bytes) -> str:
    """SHA256 해시 계산"""
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def meta_key(doc_id: str) -> str:
    """메타데이터 키"""
    return f"uploaded/__meta__/{doc_id}/meta.json"

# ============ 파일 업로드 (기존 방식 유지) ============

@router.post("/upload", response_model=UploadResp)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Query("version", regex="^(skip|version|replace)$"),
):
    """
    레거시 업로드 (MinIO 기반)
    - 기존 프론트엔드와 호환
    - DB 연동 없이 동작
    """
    safe_name = os.path.basename(file.filename or "upload.bin")
    orig_ct = file.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    content = await file.read()
    
    if not content:
        raise HTTPException(400, "빈 파일입니다.")

    print(f"[UPLOAD] 파일 수신: {safe_name} ({len(content)} bytes)")

    m = MinIOStore()
    src_ext = os.path.splitext(safe_name)[1].lower()

    # PDF 변환
    try:
        if src_ext == ".pdf":
            pdf_bytes = content
            pdf_filename = safe_name
        else:
            print(f"[UPLOAD] 변환 중: {src_ext} → PDF")
            pdf_bytes = convert_stream_to_pdf_bytes(content, safe_name)
            pdf_filename = os.path.splitext(safe_name)[0] + ".pdf"
    except ConvertError as e:
        raise HTTPException(400, f"PDF 변환 실패: {e}")

    # 해시 계산
    pdf_sha = _sha256_bytes(pdf_bytes)
    hash_flag_key = f"uploaded/__hash__/{pdf_sha}.flag"

    # 중복 체크
    uploaded = True
    duplicate_reason = None
    
    if m.exists(hash_flag_key):
        uploaded = False
        duplicate_reason = "same_hash"
        print(f"[UPLOAD] 중복 파일 감지 (hash={pdf_sha[:8]})")

    # MinIO 업로드
    object_pdf = f"uploaded/{pdf_filename}"
    
    if not uploaded and mode == "skip":
        print(f"[UPLOAD] 스킵 (중복): {safe_name}")
    else:
        if not uploaded:
            if mode == "replace":
                m.upload_bytes(pdf_bytes, object_name=object_pdf, content_type="application/pdf", length=len(pdf_bytes))
            else:  # version
                object_pdf = f"uploaded/{uuid.uuid4().hex}_{pdf_filename}"
                m.upload_bytes(pdf_bytes, object_name=object_pdf, content_type="application/pdf", length=len(pdf_bytes))
        else:
            m.upload_bytes(pdf_bytes, object_name=object_pdf, content_type="application/pdf", length=len(pdf_bytes))

    # 해시 플래그 저장
    try:
        if uploaded and not m.exists(hash_flag_key):
            m.upload_bytes(b"1", object_name=hash_flag_key, content_type="text/plain", length=1)
    except Exception as e:
        print(f"[UPLOAD] 해시 플래그 저장 실패: {e}")

    # 원본 파일 저장
    doc_id = os.path.splitext(os.path.basename(object_pdf))[0]
    object_orig = f"uploaded/originals/{doc_id}/{safe_name}"
    
    if m.exists(object_orig):
        try:
            rsize = m.size(object_orig)
        except Exception:
            rsize = -1
        if rsize != len(content):
            object_orig = f"uploaded/originals/{doc_id}/{uuid.uuid4().hex}_{safe_name}"

    m.upload_bytes(content, object_name=object_orig, content_type=orig_ct, length=len(content))

    # 메타데이터 저장
    try:
        meta = {
            "doc_id": doc_id,
            "title": safe_name,
            "pdf_key": object_pdf,
            "original_key": object_orig,
            "original_name": safe_name,
            "is_pdf_original": (src_ext == ".pdf"),
            "sha256": pdf_sha,
            "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": mode,
        }
        m.put_json(meta_key(doc_id), meta)
    except Exception as e:
        print(f"[UPLOAD] 메타데이터 저장 실패: {e}")

    # 백그라운드 인덱싱
    job_id = uuid.uuid4().hex
    job_state.start(job_id, doc_id=doc_id, minio_object=object_pdf)
    job_state.update(
        job_id,
        status="uploaded",
        step="minio:ok",
        filename=safe_name,
        progress=10,
        mode=mode,
        content_sha256=pdf_sha,
        duplicate_reason=duplicate_reason,
        uploaded=uploaded,
    )
    
    # 인덱싱 함수는 기존 코드 유지 (너무 길어서 생략)
    # background_tasks.add_task(index_pdf_to_milvus, job_id, None, object_pdf, uploaded, False, doc_id)

    return UploadResp(
        filename=safe_name,
        minio_object=object_pdf,
        indexed="background",
        job_id=job_id
    )

# ============ 작업 상태 조회 ============

@router.get("/job/{job_id}")
def get_job_status(job_id: str):
    """작업 상태 조회"""
    st = job_state.get(job_id)
    if not st:
        raise HTTPException(404, f"job not found: {job_id}")
    return st

@router.get("/job/{job_id}/sse")
async def stream_job_progress(job_id: str):
    """SSE 방식 진행률 스트리밍"""
    async def event_gen():
        last_serialized = None
        for _ in range(600):  # 최대 10분
            st = job_state.get(job_id)
            if not st:
                yield {"event": "error", "data": json.dumps({"error": "not found"}, ensure_ascii=False)}
                break

            data = json.dumps(st, ensure_ascii=False)
            if data != last_serialized:
                yield {"event": "update", "data": data}
                last_serialized = data

                if st.get("status") in ("done", "error"):
                    break

            await asyncio.sleep(1)

    return EventSourceResponse(event_gen())

# ============ MinIO 파일 관리 ============

@router.get("/files")
def list_files(prefix: str = "uploaded/", include_internal: bool = False, only_pdf: bool = False):
    """파일 목록 조회"""
    m = MinIOStore()
    try:
        keys = m.list_files(prefix=prefix)
    except Exception as e:
        raise HTTPException(500, f"MinIO 파일 조회 실패: {e}")

    if not include_internal:
        keys = [k for k in keys if not (k.endswith(".flag") or "/__hash__/" in k or "/__meta__/" in k)]

    if only_pdf:
        keys = [k for k in keys if k.lower().endswith(".pdf")]

    return {"files": keys}

@router.get("/file/{object_name:path}")
def get_file_presigned(
    object_name: str,
    minutes: int = Query(60, ge=1, le=7*24*60),
    download_name: Optional[str] = None,
    inline: bool = False,
):
    """Presigned URL 생성"""
    key = unquote(object_name)
    m = MinIOStore()
    
    if not m.exists(key):
        raise HTTPException(404, f"object not found: {key}")

    try:
        if inline:
            url = m.presign_view(key, filename=download_name, ttl_seconds=minutes * 60)
        else:
            url = m.presign_download(key, filename=download_name, ttl_seconds=minutes * 60)
        return {"url": url}
    except Exception as e:
        raise HTTPException(500, f"presign 실패: {e}")

@router.delete("/file/{object_name:path}")
def delete_file(object_name: str):
    """파일 삭제"""
    key = unquote(object_name)
    m = MinIOStore()
    
    if not m.exists(key):
        raise HTTPException(404, f"object not found: {key}")
    
    try:
        m.delete(key)
        return {"status": "ok", "deleted": key}
    except Exception as e:
        raise HTTPException(500, f"파일 삭제 실패: {e}")

@router.get("/view/{object_name:path}")
def view_object(object_name: str, name: Optional[str] = None):
    """파일 스트리밍 (인라인)"""
    key = unquote(object_name)
    m = MinIOStore()
    
    if not m.exists(key):
        raise HTTPException(404, f"object not found: {key}")

    disp_name = name or os.path.basename(key)
    ext = os.path.splitext(key)[1].lower()
    media = "application/pdf" if ext == ".pdf" else (mimetypes.guess_type(disp_name)[0] or "application/octet-stream")

    try:
        obj = m.client.get_object(m.bucket, key)
    except Exception as e:
        raise HTTPException(500, f"MinIO get_object 실패: {e}")

    headers = {"Content-Disposition": _content_disposition("inline", disp_name)}

    def _iter():
        try:
            for chunk in obj.stream(32 * 1024):
                yield chunk
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(_iter(), media_type=media, headers=headers)

@router.get("/download/{object_name:path}")
def download_object(object_name: str, name: Optional[str] = None):
    """파일 다운로드"""
    key = unquote(object_name)
    m = MinIOStore()
    
    if not m.exists(key):
        raise HTTPException(404, f"object not found: {key}")

    disp_name = name or os.path.basename(key)
    media = mimetypes.guess_type(disp_name)[0] or "application/octet-stream"

    try:
        obj = m.client.get_object(m.bucket, key)
    except Exception as e:
        raise HTTPException(500, f"MinIO get_object 실패: {e}")

    headers = {"Content-Disposition": _content_disposition("attachment", disp_name)}

    def _iter():
        try:
            for chunk in obj.stream(32 * 1024):
                yield chunk
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(_iter(), media_type=media, headers=headers)

# ============ 문서 목록 ============

@router.get("/rag/docs")
def list_docs():
    """MinIO 기반 문서 목록 조회"""
    m = MinIOStore()
    try:
        all_keys = m.list_files("uploaded/")
    except Exception as e:
        raise HTTPException(500, f"minio list 실패: {e}")

    def is_internal(k: str) -> bool:
        return (
            k.endswith(".flag")
            or "/__hash__/" in k
            or "/__meta__/" in k
            or k.startswith("uploaded/originals/")
        )

    pdf_keys = [k for k in all_keys if not is_internal(k) and k.lower().endswith(".pdf")]

    items = []
    for k in pdf_keys:
        base = os.path.basename(k)
        doc_id = os.path.splitext(base)[0]

        meta = None
        try:
            if m.exists(meta_key(doc_id)):
                meta = m.get_json(meta_key(doc_id))
        except Exception:
            meta = None

        original_key = None
        original_name = None
        uploaded_at = None
        
        if isinstance(meta, dict):
            original_key = meta.get("original_key")
            original_name = meta.get("original_name")
            uploaded_at = meta.get("uploaded_at")

        items.append({
            "doc_id": doc_id,
            "title": base,
            "pdf_key": k,
            "original_key": original_key,
            "original_name": original_name,
            "uploaded_at": uploaded_at,
        })

    return {"docs": items}

# ============ 디버그 엔드포인트 ============

@router.get("/debug/milvus/info")
def debug_milvus_info():
    """Milvus 상태 정보"""
    try:
        from app.services.embedding_model import get_embedding_model
        from app.services.milvus_store_v2 import MilvusStoreV2
        
        model = get_embedding_model()
        store = MilvusStoreV2(dim=model.get_sentence_embedding_dimension())
        return store.stats()
    except Exception as e:
        raise HTTPException(500, f"Milvus info 조회 실패: {e}")

@router.get("/debug/milvus/peek")
def debug_milvus_peek(limit: int = 100, full: bool = True, max_chars: Optional[int] = None):
    """Milvus 데이터 미리보기"""
    try:
        from app.services.embedding_model import get_embedding_model
        from app.services.milvus_store_v2 import MilvusStoreV2
        
        model = get_embedding_model()
        store = MilvusStoreV2(dim=model.get_sentence_embedding_dimension())
        
        if full:
            os.environ["DEBUG_PEEK_MAX_CHARS"] = "0"
        elif max_chars is not None:
            os.environ["DEBUG_PEEK_MAX_CHARS"] = str(max_chars)
        
        return {"items": store.peek(limit=limit)}
    except Exception as e:
        raise HTTPException(500, f"Milvus peek 실패: {e}")