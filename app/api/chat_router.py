# app/api/chat_router.py
"""
챗봇 전용 라우터 (기존 llama_router에서 분리)
- 질문/답변 기능
- RAG 검색 및 리랭킹
"""
from __future__ import annotations
import os
import re
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.llama_model import generate_answer_unified
from app.services.milvus_store_v2 import MilvusStoreV2
from app.services.embedding_model import get_embedding_model, embed
from app.services.reranker import rerank

router = APIRouter(prefix="/chat", tags=["chat"])

# ============ Request/Response 모델 ============

class GenerateReq(BaseModel):
    prompt: str
    model_name: str = "llama-3.2-3b"

class AskReq(BaseModel):
    question: str
    model_name: str = "llama-3.2-3b"
    top_k: int = 3

class AskResp(BaseModel):
    answer: str
    used_chunks: int
    sources: Optional[List[dict]] = None

# ============ 헬퍼 함수 ============

def _strip_meta_line(chunk_text: str) -> str:
    """청크 맨 위 META: 라인 제거"""
    t = chunk_text or ""
    if t.startswith("META:"):
        nl = t.find("\n")
        t = t[nl+1:] if nl != -1 else ""
    return t.strip()

_DEF_PATTS = ("뭐야", "무엇", "뭔가", "의미", "정의", "설명", "어떤", "무엇인가", "무엇인지")

def normalize_query(q: str) -> str:
    """
    정의/설명형 질문을 검색 친화적으로 보강
    """
    base = q.strip()
    if any(p in base for p in _DEF_PATTS):
        return f"{base} 내용 정의"
    return base

_KW_TOKEN_RE = re.compile(r"[A-Za-z가-힣0-9\.#\-]+")

def extract_keywords(q: str) -> list[str]:
    """질문에서 검색 키워드 추출"""
    toks = [t for t in _KW_TOKEN_RE.findall(q) if len(t) >= 2]
    seen, out = set(), []
    for t in toks:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            out.append(t)
    return out

# ============ 엔드포인트 ============

@router.get("/test")
def test():
    """테스트 엔드포인트"""
    return {"status": "Chat router is working"}

@router.post("/generate")
def generate(body: GenerateReq):
    """LLM 직접 생성 (RAG 없이)"""
    try:
        result = generate_answer_unified(body.prompt, body.model_name)
        return {"response": result}
    except Exception as e:
        raise HTTPException(500, f"모델 응답 생성 실패: {e}")

@router.post("/ask", response_model=AskResp)
def ask_question(req: AskReq):
    """
    RAG 기반 질문 답변
    1. 쿼리 보강
    2. Milvus 검색
    3. 키워드 부스팅
    4. 리랭킹
    5. LLM 생성
    """
    try:
        # 0) 모델/스토어 준비
        model = get_embedding_model()
        store = MilvusStoreV2(dim=model.get_sentence_embedding_dimension())

        # 1) 질문 전처리 + 초기 검색
        query_for_search = normalize_query(req.question)
        raw_topk = max(20, req.top_k * 5)
        cands = store.search(query_for_search, embed_fn=embed, topk=raw_topk)

        if not cands:
            return AskResp(
                answer="업로드된 문서에서 관련 내용을 찾을 수 없습니다.",
                used_chunks=0,
                sources=[]
            )

        # 2) 키워드 부스트
        kws = extract_keywords(req.question)
        
        def _kw_boost_score(c: dict) -> int:
            txt = _strip_meta_line(c.get("chunk", "")).lower()
            return sum(1 for k in kws if k.lower() in txt)
        
        for c in cands:
            c["kw_boost"] = _kw_boost_score(c)

        # 조항 부스트 (예: "제57조")
        ARTICLE_BOOST = float(os.getenv("RAG_ARTICLE_BOOST", "2.5"))
        m = re.search(r"제\s*(\d+)\s*조", req.question)
        if m:
            art = m.group(1)
            patt = re.compile(rf"제\s*{art}\s*조")
            for c in cands:
                sec = c.get("section") or ""
                txt = c.get("chunk") or ""
                if patt.search(sec) or patt.search(txt):
                    c["kw_boost"] = c.get("kw_boost", 0.0) + ARTICLE_BOOST

        # 정렬: 키워드 부스트 → 유사도 점수
        cands.sort(key=lambda x: (x.get("kw_boost", 0), x.get("score", 0.0)), reverse=True)

        # 3) 리랭크
        topk = rerank(req.question, cands, top_k=req.top_k)
        if not topk:
            return AskResp(
                answer="문서에서 신뢰할 수 있는 관련 내용을 찾지 못했습니다.",
                used_chunks=0,
                sources=[]
            )

        # 4) 컨텍스트 구성
        context_parts = []
        for i, chunk in enumerate(topk, 1):
            text = _strip_meta_line(chunk.get("chunk", ""))
            section = chunk.get("section", "")
            page = chunk.get("page", "")
            
            header = f"[문서 {i}"
            if section:
                header += f" - {section}"
            if page:
                header += f" (p.{page})"
            header += "]"
            
            context_parts.append(f"{header}\n{text}")

        context = "\n\n".join(context_parts)

        # 5) 프롬프트 생성
        prompt = f"""다음 문서 내용을 참고하여 질문에 답변해주세요.

문서 내용:
{context}

질문: {req.question}

답변:"""

        # 6) LLM 생성
        answer = generate_answer_unified(prompt, req.model_name)

        # 7) 소스 정보 구성
        sources = []
        for chunk in topk:
            sources.append({
                "doc_id": chunk.get("doc_id"),
                "page": chunk.get("page"),
                "section": chunk.get("section"),
                "score": chunk.get("score"),
                "text_preview": _strip_meta_line(chunk.get("chunk", ""))[:200] + "..."
            })

        return AskResp(
            answer=answer,
            used_chunks=len(topk),
            sources=sources
        )

    except Exception as e:
        raise HTTPException(500, f"질문 처리 실패: {e}")

@router.get("/stats")
def get_chat_stats():
    """챗봇 관련 통계"""
    try:
        model = get_embedding_model()
        store = MilvusStoreV2(dim=model.get_sentence_embedding_dimension())
        stats = store.stats()
        
        return {
            "total_chunks": stats.get("row_count", 0),
            "total_docs": len(store.list_all_docs()) if hasattr(store, 'list_all_docs') else 0,
            "model_name": "llama-3.2-3b",
            "embedding_dim": model.get_sentence_embedding_dimension()
        }
    except Exception as e:
        raise HTTPException(500, f"통계 조회 실패: {e}")