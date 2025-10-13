# app/models/db_models.py
"""
자바단 DB 스키마 모델
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class FileMetadata(BaseModel):
    """파일 메타데이터 (자바단 file_metadata 테이블)"""
    data_id: str = Field(..., description="문서 고유 ID")
    data_title: str = Field(..., description="문서 제목")
    data_code: Optional[str] = Field(None, description="분류 코드")
    data_code_detail: Optional[str] = Field(None, description="분류 코드 상세")
    data_code_detail_sub: Optional[str] = Field(None, description="분류 코드 서브")
    file_folder: Optional[str] = Field(None, description="파일 경로/폴더")
    file_id: str = Field(..., description="파일 ID")
    
    # 등록 정보
    reg_nm: str = Field(..., description="등록자명")
    reg_id: str = Field(..., description="등록자 ID")
    reg_dt: datetime = Field(..., description="등록일시")
    reg_type: str = Field(..., description="등록 타입")
    
    # 수정 정보
    upt_nm: Optional[str] = Field(None, description="수정자명")
    upt_id: Optional[str] = Field(None, description="수정자 ID")
    upt_dt: Optional[datetime] = Field(None, description="수정일시")
    upt_type: Optional[str] = Field(None, description="수정 타입")
    
    # 삭제 정보
    del_nm: Optional[str] = Field(None, description="삭제자명")
    del_id: Optional[str] = Field(None, description="삭제자 ID")
    del_dt: Optional[datetime] = Field(None, description="삭제일시")
    del_yn: str = Field(default='N', description="삭제 여부 (Y/N)")
    del_type: Optional[str] = Field(None, description="삭제 타입")
    
    # 파싱 정보
    parse_yn: str = Field(default='N', description="파싱 완료 여부 (Y/N)")
    parse_start_dt: Optional[datetime] = Field(None, description="파싱 시작일시")
    parse_end_dt: Optional[datetime] = Field(None, description="파싱 종료일시")
    
    # 링크 정보
    link_data_id: Optional[str] = Field(None, description="연결 문서 ID")
    main_yn: str = Field(default='N', description="메인 여부 (Y/N)")
    
    # 🆕 신규 컬럼
    manual_edit_yn: str = Field(default='N', description="수기 편집 체크박스 (Y/N)")
    ocr_failed_yn: str = Field(default='N', description="OCR 실패 플래그 (Y/N)")
    rag_completed_yn: str = Field(default='N', description="RAG 처리 완료 여부 (Y/N)")

class OCRResult(BaseModel):
    """OCR 결과 (자바단 ocr_results 테이블)"""
    idx: Optional[int] = Field(None, description="인덱스 (auto_increment)")
    data_id: str = Field(..., description="문서 ID (FK)")
    page: int = Field(..., description="페이지 번호")
    text: str = Field(..., description="OCR 텍스트")
    parse_dt: datetime = Field(..., description="파싱일시")
    upt_dt: Optional[datetime] = Field(None, description="수정일시")
    
    # 🆕 텍스트 분류 (자바단에서 추가 가능)
    text_sc_end: Optional[str] = Field(None, description="텍스트 끝 구분")
    text_sc_head: Optional[str] = Field(None, description="텍스트 머리 구분")
    text_sc_middle: Optional[str] = Field(None, description="텍스트 중간 구분")

class RAGProcessLog(BaseModel):
    """RAG 처리 로그 (선택적 - 이력 추적용)"""
    log_id: Optional[int] = None
    data_id: str
    process_type: str  # 'auto_ocr', 'manual_edit', 'rag_indexing'
    status: str  # 'pending', 'processing', 'success', 'failed'
    started_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    chunk_count: Optional[int] = None