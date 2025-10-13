# app/services/rag_orchestrator.py
"""
RAG 오케스트레이터 - 자바 DB 기반 워크플로우
"""
from typing import List, Tuple, Dict, Optional
from datetime import datetime
import traceback
import os

from app.services.db_connector import DBConnector
from app.services.file_parser import parse_pdf_blocks_from_bytes
from app.services.pdf_converter import convert_bytes_to_pdf_bytes, ConvertError
from app.services.law_chunker import LawChunker
from app.services.layout_chunker import LayoutChunker
from app.services.chunker import SmartChunker
from app.services.milvus_store_v2 import MilvusStoreV2
from app.services.embedding_model import get_embedding_model
from app.services.minio_store import MinIOStore

class RAGOrchestrator:
    """DB 기반 RAG 파이프라인 오케스트레이터"""
    
    def __init__(self):
        print("🔧 RAG Orchestrator 초기화 중...")
        self.db = DBConnector()
        self.minio = MinIOStore()
        self.embed_model = get_embedding_model()
        self.milvus = MilvusStoreV2(dim=self.embed_model.dim)
        
        # 청킹 전략 초기화
        self.law_chunker = LawChunker(
            encoder_fn=self.embed_model.encode_fn,
            target_tokens=int(os.getenv('CHUNK_TARGET_TOKENS', 400)),
            overlap_tokens=int(os.getenv('CHUNK_OVERLAP_TOKENS', 100))
        )
        self.layout_chunker = LayoutChunker(
            encoder_fn=self.embed_model.encode_fn,
            target_tokens=int(os.getenv('CHUNK_TARGET_TOKENS', 400)),
            overlap_tokens=int(os.getenv('CHUNK_OVERLAP_TOKENS', 100))
        )
        self.smart_chunker = SmartChunker(
            encoder_fn=self.embed_model.encode_fn,
            target_tokens=int(os.getenv('CHUNK_TARGET_TOKENS', 400)),
            overlap_tokens=int(os.getenv('CHUNK_OVERLAP_TOKENS', 100))
        )
        print("✅ RAG Orchestrator 초기화 완료")
    
    # ============ 메인 처리 함수들 ============
    
    def process_auto_ocr_files(self, limit: int = 10) -> Dict[str, int]:
        """
        자동 OCR 파일 처리
        1. DB에서 대기 파일 조회
        2. MinIO에서 다운로드
        3. PDF 변환
        4. OCR 실행
        5. DB에 OCR 결과 저장
        6. RAG 파이프라인 실행
        """
        pending = self.db.get_pending_auto_files(limit=limit)
        results = {'success': 0, 'failed': 0, 'skipped': 0}
        
        print(f"📋 자동 OCR 대기 파일: {len(pending)}개")
        
        for file_meta in pending:
            data_id = file_meta['data_id']
            data_title = file_meta.get('data_title', data_id)
            
            try:
                print(f"\n🔄 처리 시작: [{data_id}] {data_title}")
                
                # 1. 파일 다운로드
                file_bytes = self._download_file(file_meta)
                print(f"  ✓ 파일 다운로드 완료 ({len(file_bytes)} bytes)")
                
                # 2. PDF 변환
                pdf_bytes = self._convert_to_pdf(file_bytes, file_meta)
                print(f"  ✓ PDF 변환 완료 ({len(pdf_bytes)} bytes)")
                
                # 3. OCR 시작
                self.db.update_parse_status(data_id, 'N', start_dt=datetime.now())
                
                # 4. OCR 실행
                ocr_pages = self._run_ocr(pdf_bytes)
                print(f"  ✓ OCR 완료 ({len(ocr_pages)} pages)")
                
                # 5. DB에 OCR 결과 저장
                self.db.bulk_insert_ocr_results(data_id, ocr_pages)
                print(f"  ✓ DB 저장 완료")
                
                # 6. OCR 완료 상태 업데이트
                self.db.update_parse_status(data_id, 'Y', end_dt=datetime.now())
                
                # 7. RAG 파이프라인
                self._process_rag_pipeline(data_id, file_meta, ocr_pages)
                
                results['success'] += 1
                print(f"  ✅ 완료: [{data_id}]")
                
            except ConvertError as e:
                print(f"  ❌ 변환 실패: {e}")
                self.db.update_parse_status(
                    data_id, 'N', 
                    end_dt=datetime.now(), 
                    ocr_failed=True
                )
                results['failed'] += 1
                
            except Exception as e:
                print(f"  ❌ 처리 실패: {e}")
                traceback.print_exc()
                self.db.update_parse_status(
                    data_id, 'N', 
                    end_dt=datetime.now(), 
                    ocr_failed=True
                )
                results['failed'] += 1
        
        return results
    
    def process_manual_edit_files(self, limit: int = 10) -> Dict[str, int]:
        """
        수기 편집 완료 파일 처리
        1. DB에서 텍스트 데이터 조회
        2. RAG 파이프라인 실행
        """
        manual_files = self.db.get_manual_edit_completed(limit=limit)
        results = {'success': 0, 'failed': 0, 'skipped': 0}
        
        print(f"📋 수기 편집 완료 파일: {len(manual_files)}개")
        
        for file_meta in manual_files:
            data_id = file_meta['data_id']
            data_title = file_meta.get('data_title', data_id)
            
            # Milvus에 이미 존재하는지 확인
            if self.milvus.doc_exists(data_id):
                print(f"  ⏭️ 스킵 (이미 처리됨): [{data_id}]")
                self.db.update_rag_completed(data_id, True)
                results['skipped'] += 1
                continue
            
            try:
                print(f"\n🔄 수기 편집 처리: [{data_id}] {data_title}")
                
                # OCR 결과 조회
                ocr_results = self.db.get_ocr_results(data_id)
                if not ocr_results:
                    raise ValueError("OCR 결과 없음")
                
                ocr_pages = [(r['page'], r['text']) for r in ocr_results]
                print(f"  ✓ DB에서 텍스트 조회 ({len(ocr_pages)} pages)")
                
                # RAG 파이프라인
                self._process_rag_pipeline(data_id, file_meta, ocr_pages)
                
                results['success'] += 1
                print(f"  ✅ 완료: [{data_id}]")
                
            except Exception as e:
                print(f"  ❌ 처리 실패: {e}")
                traceback.print_exc()
                results['failed'] += 1
        
        return results
    
    # ============ 내부 헬퍼 함수들 ============
    
    def _download_file(self, file_meta: Dict) -> bytes:
        """MinIO에서 파일 다운로드"""
        file_path = file_meta.get('file_folder') or file_meta.get('file_id')
        if not file_path:
            raise ValueError("file_folder 또는 file_id 없음")
        
        if not self.minio.exists(file_path):
            raise FileNotFoundError(f"MinIO에 파일 없음: {file_path}")
        
        return self.minio.download_bytes(file_path)
    
    def _convert_to_pdf(self, file_bytes: bytes, file_meta: Dict) -> bytes:
        """PDF 변환 (필요시)"""
        filename = file_meta.get('data_title', 'file.pdf')
        ext = os.path.splitext(filename)[1].lower()
        
        if ext == '.pdf':
            return file_bytes
        
        # DOCX, HWPX 등 변환
        print(f"  ⚙️ PDF 변환 중: {ext} → PDF")
        return convert_bytes_to_pdf_bytes(file_bytes, filename)
    
    def _run_ocr(self, pdf_bytes: bytes) -> List[Tuple[int, str]]:
        """OCR 실행 - 레이아웃 블록 기반"""
        try:
            blocks_by_page = parse_pdf_blocks_from_bytes(pdf_bytes)
            pages = []
            
            for page_no, blocks in blocks_by_page:
                # 블록을 텍스트로 결합
                text = '\n'.join(block.get('text', '') for block in blocks if block.get('text'))
                pages.append((page_no, text))
            
            return pages
        except Exception as e:
            raise RuntimeError(f"OCR 실패: {e}") from e
    
    def _process_rag_pipeline(
        self, 
        data_id: str, 
        file_meta: Dict, 
        ocr_pages: List[Tuple[int, str]]
    ):
        """
        RAG 파이프라인: 청킹 → 임베딩 → Milvus
        """
        if not ocr_pages:
            raise ValueError("OCR 결과가 비어있음")
        
        # 1. 문서 타입 판별
        doc_type = self._detect_document_type(file_meta, ocr_pages)
        print(f"  📄 문서 타입: {doc_type}")
        
        # 2. 타입별 청킹
        chunks = self._chunk_by_type(doc_type, ocr_pages)
        print(f"  ✂️ 청킹 완료: {len(chunks)} chunks")
        
        if not chunks:
            raise ValueError("청킹 결과 없음")
        
        # 3. Milvus 저장 (임베딩 포함)
        self.milvus.add_document(
            doc_id=data_id,
            chunks=chunks,
            embed_fn=self.embed_model.encode
        )
        print(f"  💾 Milvus 저장 완료")
        
        # 4. RAG 완료 플래그 업데이트
        self.db.update_rag_completed(data_id, True)
        print(f"  ✅ RAG 처리 완료")
    
    def _detect_document_type(
        self, 
        file_meta: Dict, 
        pages: List[Tuple[int, str]]
    ) -> str:
        """
        문서 타입 판별
        - 'law': 법령, 규정 (조항 구조)
        - 'manual': 매뉴얼, 절차서
        - 'general': 일반 문서
        """
        title = file_meta.get('data_title', '').lower()
        code = file_meta.get('data_code', '').lower()
        
        # 메타데이터 기반 판별
        law_keywords = ['법', '조', '규정', '령', 'infcirc', 'iaea', 'regulation', 'act']
        manual_keywords = ['매뉴얼', 'manual', '절차서', 'procedure', '지침', 'guideline']
        
        if any(kw in title or kw in code for kw in law_keywords):
            return 'law'
        elif any(kw in title or kw in code for kw in manual_keywords):
            return 'manual'
        
        # 내용 기반 휴리스틱 (첫 3페이지 샘플링)
        sample_text = ' '.join(text for _, text in pages[:3]).lower()
        
        # 조항 패턴 검사
        import re
        article_patterns = [
            r'제\s*\d+\s*조',  # 제1조
            r'제\s*\d+\s*절',  # 제1절
            r'제\s*\d+\s*항',  # 제1항
            r'infcirc[/\-]\d+',  # INFCIRC/153
        ]
        
        if any(re.search(pattern, sample_text) for pattern in article_patterns):
            return 'law'
        
        # 매뉴얼 패턴 검사
        manual_patterns = [
            r'\d+\.\d+\.\s',  # 1.1. 제목
            r'절차\s*\d+',  # 절차 1
            r'step\s*\d+',  # Step 1
        ]
        
        if any(re.search(pattern, sample_text) for pattern in manual_patterns):
            return 'manual'
        
        return 'general'
    
    def _chunk_by_type(
        self, 
        doc_type: str, 
        pages: List[Tuple[int, str]]
    ) -> List[Tuple[str, Dict]]:
        """타입별 청킹 전략 선택"""
        if doc_type == 'law':
            print(f"  🔧 Law Chunker 사용")
            return self.law_chunker.chunk_pages(pages)
        elif doc_type == 'manual':
            print(f"  🔧 Layout Chunker 사용")
            return self.layout_chunker.chunk_pages(pages)
        else:
            print(f"  🔧 Smart Chunker 사용")
            return self.smart_chunker.chunk_pages(pages)
    
    # ============ 유틸리티 ============
    
    def retry_failed_file(self, data_id: str) -> bool:
        """OCR 실패 파일 재시도"""
        try:
            # 플래그 리셋
            self.db.reset_ocr_failed(data_id)
            print(f"🔄 재시도 준비 완료: {data_id}")
            
            # 재처리
            file_meta = self.db.get_file_by_id(data_id)
            if not file_meta:
                raise ValueError("파일 메타데이터 없음")
            
            # 자동 OCR 플로우로 재처리
            results = self.process_auto_ocr_files(limit=1)
            return results['success'] > 0
            
        except Exception as e:
            print(f"❌ 재시도 실패: {e}")
            return False
    
    def delete_and_reindex(self, data_id: str) -> bool:
        """문서 삭제 후 재인덱싱"""
        try:
            # Milvus에서 삭제
            deleted = self.milvus.delete_document(data_id)
            print(f"🗑️ Milvus에서 삭제: {deleted} chunks")
            
            # RAG 완료 플래그 리셋
            self.db.update_rag_completed(data_id, False)
            
            # 재인덱싱
            file_meta = self.db.get_file_by_id(data_id)
            if not file_meta:
                raise ValueError("파일 메타데이터 없음")
            
            if file_meta['manual_edit_yn'] == 'Y':
                results = self.process_manual_edit_files(limit=1)
            else:
                results = self.process_auto_ocr_files(limit=1)
            
            return results['success'] > 0
            
        except Exception as e:
            print(f"❌ 재인덱싱 실패: {e}")
            return False
    
    def get_processing_status(self, data_id: str) -> Dict:
        """문서 처리 상태 조회"""
        file_meta = self.db.get_file_by_id(data_id)
        if not file_meta:
            return {'status': 'not_found'}
        
        ocr_count = len(self.db.get_ocr_results(data_id))
        in_milvus = self.milvus.doc_exists(data_id)
        
        return {
            'status': 'ready' if in_milvus else 'processing',
            'data_id': data_id,
            'data_title': file_meta.get('data_title'),
            'manual_edit': file_meta['manual_edit_yn'] == 'Y',
            'parse_completed': file_meta['parse_yn'] == 'Y',
            'ocr_failed': file_meta['ocr_failed_yn'] == 'Y',
            'rag_completed': file_meta['rag_completed_yn'] == 'Y',
            'ocr_pages': ocr_count,
            'in_milvus': in_milvus,
            'parse_start': file_meta.get('parse_start_dt'),
            'parse_end': file_meta.get('parse_end_dt'),
        }