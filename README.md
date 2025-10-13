# nuclear_rag_system

nuclar version RAG system(with. JAVA)

기존 RAG_LAND 와 다른 점
nuclear_rag_system/
├── app/
│ ├── models/
│ │ ├── **init**.py
│ │ └── db_models.py # 🆕 DB 스키마 정의
│ ├── services/
│ │ ├── **init**.py
│ │ ├── db_connector.py # 🆕 자바 DB 연결
│ │ ├── rag_orchestrator.py # 🆕 전체 워크플로우 관리
│ │ ├── file_parser.py # 유지 (OCR)
│ │ ├── pdf_converter.py # 유지 (변환)
│ │ ├── chunker.py # 유지
│ │ ├── law_chunker.py # 유지
│ │ ├── layout_chunker.py # 유지
│ │ ├── embedding_model.py # 유지
│ │ ├── milvus_store_v2.py # 수정 (메서드 추가)
│ │ ├── reranker.py # 유지
│ │ └── llama_model.py # 유지
│ ├── api/
│ │ ├── **init**.py
│ │ ├── rag_router.py # 🆕 RAG 전용 엔드포인트
│ │ └── chat_router.py # 🆕 챗봇 전용 (기존 llama_router 분리)
│ ├── utils/
│ │ ├── **init**.py
│ │ └── logger.py # 🆕 로깅 유틸
│ └── main.py # 수정 (백그라운드 폴링 추가)
├── tests/
│ ├── test_db_connector.py # 🆕
│ ├── test_rag_orchestrator.py # 🆕
│ └── test_integration.py # 🆕
├── milvus-docker/
│ ├── docker-compose.yml # 수정
│ ├── Dockerfile # 유지
│ └── requirements.txt # 수정 (pymysql 추가)
├── .env # 수정 (DB 설정 추가)
└── README.md # 업데이트
