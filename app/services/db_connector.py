# app/services/db_connector.py
"""
자바단 DB 연결 모듈 (MySQL/PostgreSQL 지원)
"""
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
from contextlib import contextmanager
import pymysql
from pymysql.cursors import DictCursor

class DBConnector:
    """자바단 DB 연결 클래스"""
    
    def __init__(self):
        self.config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'nuclear_rag'),
            'charset': 'utf8mb4',
            'connect_timeout': 10,
            'read_timeout': 30,
            'write_timeout': 30,
        }
        print(f"✅ DB 설정: {self.config['user']}@{self.config['host']}:{self.config['port']}/{self.config['database']}")
    
    @contextmanager
    def get_connection(self):
        """DB 연결 컨텍스트 매니저"""
        conn = None
        try:
            conn = pymysql.connect(**self.config, cursorclass=DictCursor)
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            raise e
        finally:
            if conn:
                conn.close()
    
    # ============ 파일 메타데이터 조회 ============
    
    def get_pending_auto_files(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        자동 OCR 대기 파일 조회
        - manual_edit_yn='N' (체크박스 체크 안함)
        - parse_yn='N' (아직 OCR 안함)
        - ocr_failed_yn='N' (실패 이력 없음)
        - del_yn='N' (삭제 안됨)
        """
        query = """
        SELECT * FROM file_metadata
        WHERE manual_edit_yn = 'N'
          AND parse_yn = 'N'
          AND ocr_failed_yn = 'N'
          AND del_yn = 'N'
        ORDER BY reg_dt ASC
        LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (limit,))
                return cursor.fetchall()
    
    def get_manual_edit_completed(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        수기 편집 완료 파일 조회
        - manual_edit_yn='Y' (체크박스 체크됨)
        - parse_yn='Y' (자바단에서 텍스트 입력 완료)
        - rag_completed_yn='N' (아직 RAG 처리 안됨)
        """
        query = """
        SELECT * FROM file_metadata
        WHERE manual_edit_yn = 'Y'
          AND parse_yn = 'Y'
          AND rag_completed_yn = 'N'
          AND del_yn = 'N'
        ORDER BY parse_end_dt ASC
        LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (limit,))
                return cursor.fetchall()
    
    def get_ocr_failed_files(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        OCR 실패 파일 조회 (재시도 대상)
        - ocr_failed_yn='Y'
        - parse_yn='N'
        """
        query = """
        SELECT * FROM file_metadata
        WHERE ocr_failed_yn = 'Y'
          AND parse_yn = 'N'
          AND del_yn = 'N'
        ORDER BY parse_end_dt DESC
        LIMIT %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (limit,))
                return cursor.fetchall()
    
    def get_file_by_id(self, data_id: str) -> Optional[Dict[str, Any]]:
        """특정 파일 메타데이터 조회"""
        query = """
        SELECT * FROM file_metadata
        WHERE data_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (data_id,))
                return cursor.fetchone()
    
    # ============ OCR 결과 조회 ============
    
    def get_ocr_results(self, data_id: str) -> List[Dict[str, Any]]:
        """특정 문서의 OCR 결과 조회 (페이지 순서대로)"""
        query = """
        SELECT * FROM ocr_results
        WHERE data_id = %s
        ORDER BY page ASC
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (data_id,))
                return cursor.fetchall()
    
    # ============ 상태 업데이트 ============
    
    def update_parse_status(
        self, 
        data_id: str, 
        parse_yn: str,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None,
        ocr_failed: bool = False
    ):
        """파싱 상태 업데이트"""
        fields = ["parse_yn = %s"]
        values = [parse_yn]
        
        if start_dt:
            fields.append("parse_start_dt = %s")
            values.append(start_dt)
        
        if end_dt:
            fields.append("parse_end_dt = %s")
            values.append(end_dt)
        
        if ocr_failed:
            fields.append("ocr_failed_yn = 'Y'")
        
        query = f"""
        UPDATE file_metadata
        SET {', '.join(fields)}
        WHERE data_id = %s
        """
        values.append(data_id)
        
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, tuple(values))
                return cursor.rowcount
    
    def update_rag_completed(self, data_id: str, completed: bool = True):
        """RAG 처리 완료 상태 업데이트"""
        query = """
        UPDATE file_metadata
        SET rag_completed_yn = %s
        WHERE data_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, ('Y' if completed else 'N', data_id))
                return cursor.rowcount
    
    def reset_ocr_failed(self, data_id: str):
        """OCR 실패 플래그 리셋 (재시도용)"""
        query = """
        UPDATE file_metadata
        SET ocr_failed_yn = 'N',
            parse_yn = 'N',
            parse_start_dt = NULL,
            parse_end_dt = NULL
        WHERE data_id = %s
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (data_id,))
                return cursor.rowcount
    
    # ============ OCR 결과 삽입 ============
    
    def insert_ocr_result(self, data_id: str, page: int, text: str):
        """OCR 결과 삽입 (중복 시 업데이트)"""
        query = """
        INSERT INTO ocr_results 
        (data_id, page, text, parse_dt)
        VALUES (%s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
        text = VALUES(text),
        upt_dt = NOW()
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (data_id, page, text))
                return cursor.rowcount
    
    def bulk_insert_ocr_results(self, data_id: str, pages: List[tuple]):
        """OCR 결과 일괄 삽입 [(page_no, text), ...]"""
        if not pages:
            return 0
        
        query = """
        INSERT INTO ocr_results 
        (data_id, page, text, parse_dt)
        VALUES (%s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
        text = VALUES(text),
        upt_dt = NOW()
        """
        data = [(data_id, page, text) for page, text in pages]
        
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.executemany(query, data)
                return cursor.rowcount
    
    # ============ 통계 조회 ============
    
    def get_statistics(self) -> Dict[str, int]:
        """전체 통계 조회"""
        query = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN parse_yn = 'Y' AND rag_completed_yn = 'Y' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN parse_yn = 'N' AND ocr_failed_yn = 'N' AND manual_edit_yn = 'N' THEN 1 ELSE 0 END) as pending_auto,
            SUM(CASE WHEN manual_edit_yn = 'Y' AND parse_yn = 'N' THEN 1 ELSE 0 END) as pending_manual,
            SUM(CASE WHEN ocr_failed_yn = 'Y' THEN 1 ELSE 0 END) as failed,
            SUM(CASE WHEN parse_yn = 'Y' AND rag_completed_yn = 'N' THEN 1 ELSE 0 END) as pending_rag
        FROM file_metadata
        WHERE del_yn = 'N'
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                result = cursor.fetchone()
                return {
                    'total': result['total'] or 0,
                    'completed': result['completed'] or 0,
                    'pending_auto': result['pending_auto'] or 0,
                    'pending_manual': result['pending_manual'] or 0,
                    'failed': result['failed'] or 0,
                    'pending_rag': result['pending_rag'] or 0,
                }
    
    # ============ 헬스 체크 ============
    
    def test_connection(self) -> bool:
        """DB 연결 테스트"""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    return result is not None
        except Exception as e:
            print(f"❌ DB 연결 실패: {e}")
            return False