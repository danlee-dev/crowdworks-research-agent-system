# 고려대학교 산학협력 프로젝트

<div align="center">
<h1>CrowdWorks Multi-Agent RAG System</h1>
<p>식품산업 전문 AI 리서치 에이전트 시스템</p>
</div>

> 개발기간: 2025.07 ~ 2025.11
>
> 협력기업: 크라우드웍스(CrowdWorks)
>
> Built with Python 3.10, FastAPI 0.116.1, Next.js 15.3.5, LangGraph 0.5.2

## 프로젝트 개요

식품산업 종사자들이 직면하는 정보 분산화와 복잡한 데이터 분석 문제를 해결하기 위해 개발된 차세대 멀티에이전트 RAG(Retrieval-Augmented Generation) 시스템입니다.

KAMIS(한국농수산물유통정보), 농림축산식품부, 농촌진흥청, 식품의약품안전처 등 여러 기관의 분산된 데이터를 통합하고, Graph RAG와 Vector RAG를 결합한 하이브리드 검색 시스템을 통해 전문적인 시장 분석과 의사결정을 지원합니다.

CrowdWorks의 AI 전처리 솔루션 Alpy를 활용한 Knowledge Compiler 시스템으로 비정형 문서를 구조화하고, 동적 멀티홉 계획(Dynamic Multi-Hop Planning)을 통해 복잡한 다단계 질의에 대한 정확한 답변을 제공합니다.

## 서비스 화면

### Web Application

식품산업 데이터의 조회, 분석, 시각화 및 리포트 생성이 가능한 AI 에이전트 웹 애플리케이션입니다.

연구자, 분석가, 식품산업 종사자가 복잡한 시장 데이터를 쉽게 이해하고 의사결정에 활용할 수 있도록 개발되었습니다.

> 접속 주소 : http://49.50.128.6:3000/

![Service UI Mockup](images/service-ui-mockup.png)

## 시스템 아키텍처

![System Architecture](images/system-architecture-diagram.png)

### 주요 구성 요소

**Data Ingestion Pipeline**
- Python 3.10 Crawler (KAMIS API, 농업 데이터)
- Playwright 1.49.1 Scraper (웹 스크래핑)
- PDF Parser (pypdf 5.1.0)

**Hybrid RAG System**
- Graph DB (Neo4j 5.15.0, APOC, GDS, Fulltext Index)
- Relational DB (PostgreSQL 14, psycopg2-binary 2.9.10)
- Vector DB (Elasticsearch 8.11.0, Nori Analyzer, Sentence Transformers 3.3.1)

**AI Agent Layer**
- Orchestrator Agent (계획 수립)
- DataGatherer Agent (데이터 수집)
- Processor Agent (데이터 처리)
- LLM & Tools (Gemini 2.5 Flash/Pro, GPT-4o, LangChain 0.3.26, Playwright 1.49.1)

**API Gateway & Orchestration**
- FastAPI 0.116.1, LangGraph 0.5.2, Uvicorn 0.35.0

**Frontend Layer**
- Web UI (Next.js 15.3.5, React 19.0.0, TypeScript 5, Tailwind CSS 4, Chart.js 4.5.0, React-Markdown 10.1.0)

**Infrastructure**
- Docker 27.5.1, Docker Compose 1.29.2
- Kibana 8.11.0 (Monitoring)
- Volume Mounts (Persistence)

**External APIs**
- KAMIS API
- arXiv API
- SERPER API
- PubMed API

## 사용자 플로우

![User Flow](images/user-flow-diagram.png)

### 질의 처리 과정

1. **User Query**: 사용자가 자연어로 복잡한 질문 입력
2. **LangGraph Workflow 실행**: Orchestrator Agent가 계획 수립
3. **3단계 병렬 처리**:
   - **Precheck 단계**: Neo4j 그래프 DB에서 엔터티 세트 생성 및 정합성 검증
   - **Graph-to-Vector 단계**: Precheck 결과를 활용한 벡터 검색 쿼리 최적화
   - **RDB Auto-SQL 단계**: 자연어를 SQL로 자동 변환하여 정형 데이터 조회
4. **DataGatherer Agent**: 내부/외부 데이터 수집
5. **Processor Agent**: 수집된 데이터 통합 및 분석
6. **결과 생성**: 사용자 친화적 차트 + 상세 텍스트 + PDF 변환 가능

![Detailed System Flow](images/detailed-system-flow.png)

## 핵심 기술

### 1. 지능형 데이터 전처리 (Knowledge Compiler 시스템)

비정형 PDF 문서를 구조화된 데이터로 변환하는 3단계 데이터 처리 파이프라인입니다.

**1단계 - PDF 문서 자동 구조 분석**
- Knowledge Compiler로 PDF → JSON 자동 변환
- OCR 기반으로 텍스트, 표, 그래프를 자동 인식 및 분리
- 표 제목-표 관계를 유지하는 계층적 메타데이터 자동 부여

**2단계 - 멀티모달 데이터 분리 및 재청킹**
- 페이지 단위로 텍스트와 표를 개별 청크로 분리
- 각 청크에 문서 제목 자동 삽입 (컨텍스트 보완)
- 메타데이터: 문서명, 출처, 페이지, 데이터 타입

**3단계 - 데이터 타입별 멀티 인덱싱**
- ElasticSearch에서 텍스트 인덱스와 표 인덱스를 분리 구축
- Hybrid Search (Sparse 0.5 + Dense 0.5)
- bge-reranker-v2-m3-ko Cross-Encoder를 통한 정밀도 향상

**청크 구조 예시**
```json
{
  "chunk_id": "doc_001_p3_table_2",
  "content": "표 데이터...",
  "metadata": {
    "doc_title": "2024 농산물 수급",
    "page": 3,
    "type": "table",
    "source": "농림부"
  }
}
```

※ Knowledge Compiler는 CrowdWorks의 AI 전처리 솔루션 Alpy를 적용
https://www.crowdworks.ai/agent/alpykc

### 2. Graph-to-Vector 증강 검색

그래프 데이터베이스의 관계 정보를 활용하여 벡터 검색의 정확도를 향상시키는 하이브리드 검색 기술입니다.

**1단계 - 관계 정보 자동 추출**
- 문서 내 엔터티와 관계(docrels) 자동 추출
- 주요 관계 예시:
  - `isfrom`: 품목-지역 생산 정보 (aTFIS 데이터)
  - `nutrients`: 품목-영양소 정보 (식약처 DB)
  - `docrels`: 문서 간 연관 정보 (예: "토마토 → 항산화 효능", 농진청 보고서)

**2단계 - Graph-To-Vector 쿼리 증강**
- GraphRAG 검색으로 관련 증거(k=50)를 선별
- LLM Prompt Engineering을 통해 VectorDB용 쿼리 재작성
- 숨겨진 연관 정보까지 자동 탐색

**3단계 - 통합 검색 구조**
```
Graph DB (Neo4j) → 관계 기반 쿼리 증강
                 ↓
Vector DB (ElasticSearch) → 의미 기반 벡터 검색
                 ↓
           풍부한 답변 생성
```

**쿼리 최적화 예시**
- Original: "토마토 영양성분과 효능"
- Graph Evidence: ["충남 생산", "비타민C", "라이코펜", "항산화"]
- Optimized: "충남 지역 토마토 비타민C 라이코펜 항산화 효능 농진청"

### 3. 동적 멀티홉 계획 (Dynamic Multi-Hop Planning)

서브쿼리 간 의존성을 분석하여 병렬 처리와 순차 처리를 자동으로 최적화하는 쿼리 실행 계획 시스템입니다.

**1단계 - 동적 멀티홉 계획**
- 서브쿼리 간 의존성 분석
- 독립 쿼리는 병렬 처리, 의존 쿼리는 순차 처리

**2단계 - 컨텍스트 치환 (Context Substitution)**
- 이전 검색 결과를 다음 단계의 입력으로 자동 변환
- 복잡한 다단계 질의에서도 컨텍스트 유지

**3단계 - Graph Pre-Check**
- 그래프 DB에서 데이터의 위치를 사전 확인
- 불필요한 검색 방지 → 속도 및 비용 최적화

**Pre-Check 결과 예시**
```json
{
  "docrels": ["충남 - 호우피해", "전남 - 침수피해", "경북 - 농작물피해"],
  "isfrom": [],
  "nutrients": [],
  "found_evidence": true,
  "precheck_disabled": false
}
```

**4단계 - Multi-Agent 협업 구조**
- Orchestrator Agent: 전체 계획 수립
- DataGatherer Agent: 병렬 검색
- Processor Agent: 데이터 처리
- Report Generator: 결과 보고서 생성

## 주요 기능

### 멀티에이전트 협업 시스템
- **Triage Agent**: 질의 유형 자동 분류 (단순 조회 vs 복잡한 분석)
- **Orchestrator Agent**: 복잡한 보고서 생성 및 시장 분석
- **Simple Answerer Agent**: 빠른 질의응답 (특정 농산물 가격 조회)
- **Worker Agents**: 전문 작업 수행 (데이터 시각화, 통계 분석)

### 하이브리드 RAG 시스템
- **Vector Search**: Elasticsearch 기반 의미론적 문서 검색
- **Graph Search**: Neo4j 기반 농산물-지역-시기-가격 관계 분석
- **SQL Query Generation**: 자연어를 SQL로 자동 변환

### 실시간 데이터 수집
- **통합 크롤러 시스템**: KAMIS API 연동으로 실시간 농수산물 가격 정보
- **실시간 스케줄링**: Neo4j Scheduler로 자동화된 데이터 수집
- **다중 데이터베이스 통합**: PostgreSQL(정형), Elasticsearch(비정형), Neo4j(관계형)

### 사용자 인터페이스
- **실시간 스트리밍**: WebSocket 기반 응답 스트리밍
- **데이터 시각화**: Chart.js 기반 인터랙티브 차트
- **PDF 리포트 생성**: 분석 결과 다운로드

## 프로젝트 구조

```
crowdworks-multiagent-system/
├── multiagent-rag-system/          # 메인 AI 애플리케이션
│   ├── backend/                    # FastAPI 서버
│   │   └── app/
│   │       ├── core/               # AI Agent 로직
│   │       │   ├── agents/         # Orchestrator, Worker Agents
│   │       │   ├── graphs/         # LangGraph Workflow
│   │       │   └── models/         # 데이터 모델
│   │       ├── services/           # 비즈니스 로직
│   │       │   ├── database/       # DB 연동 (Neo4j, Elasticsearch, PostgreSQL)
│   │       │   ├── search/         # 검색 도구
│   │       │   └── charts/         # 차트 생성
│   │       └── utils/              # 유틸리티
│   └── frontend/                   # Next.js 프론트엔드
│       └── src/
│           ├── components/         # React 컴포넌트
│           ├── hooks/              # 커스텀 훅
│           └── utils/              # 유틸리티
├── crawler_rdb/                    # 데이터 수집 크롤러
│   ├── crawler/                    # 크롤러 스크립트
│   └── services/                   # 크롤러 서비스
├── elasticsearch/                  # Vector DB 설정
│   ├── embedding.py                # 임베딩 생성
│   └── multi_index_search_engine.py
├── Neo4J/                          # Graph DB 설정
│   ├── scheduler.py                # 데이터 수집 스케줄러
│   └── graph_extract2.py           # 그래프 추출
├── utils/                          # 공통 유틸리티
│   ├── model_fallback.py           # API 폴백 시스템
│   └── api_fallback.py
└── docker-compose.yml              # 전체 시스템 통합
```

## 인프라 및 개발 환경

### 클라우드 인프라
- **클라우드 플랫폼**: Naver Cloud Platform
- **서버 스펙**: s8-g3a (vCPU 8EA, Memory 32GB)
- **운영체제**: Ubuntu 24.04-base

### 백엔드 (AI Agent Layer)
- **언어/프레임워크**: Python 3.10, FastAPI 0.116.1
- **AI 오케스트레이션**: 자체 개발 (Custom) 멀티에이전트 엔진 (Main System)
- **AI/ML 라이브러리**: LangChain 0.3.26, LangChain Core 0.3.68, Sentence Transformers 3.3.1
- **(비교 버전)**: 성능 비교를 위해 LangGraph 0.5.2 기반 실험 버전을 병행 개발 (환경 변수 `USE_LANGGRAPH`로 전환 가능)
- **LLM Integration**: LangChain-Google-GenAI 2.0.7, LangChain-OpenAI 0.2.6, Google-GenerativeAI 0.8.5

**주요 특징:**
- 기본 시스템은 자체 개발한 커스텀 멀티에이전트 엔진 사용
- LangGraph 적용 버전은 스트리밍 안정성 이슈로 실험 단계 (Feature Flag로 제어)
- `.env` 파일에서 `USE_LANGGRAPH=true` 설정 시 LangGraph 워크플로우로 전환 가능

### 프론트엔드 (Web UI Layer)
- **언어/프레임워크**: TypeScript 5, Next.js 15.3.5 (App Router), React 19.0.0
- **스타일링/시각화**: Tailwind CSS 4, Chart.js 4.5.0, React-ChartJS-2 5.3.0
- **Markdown 렌더링**: React-Markdown 10.1.0, Remark-GFM 4.0.1, Rehype-Raw 7.0.0
- **HTTP Client**: Axios 1.10.0
- **데이터 시각화**: D3.js 7.9.0

### 데이터베이스 및 저장 구조 (Hybrid RAG)
- **Relational DB**: PostgreSQL 14 (정형 데이터, 농산물 가격 정보)
- **Vector DB**: Elasticsearch 8.11.0 (비정형 텍스트/표, analysis-nori Korean Analyzer)
- **Graph DB**: Neo4j 5.15.0 (관계형 지식 그래프, APOC, Graph Data Science, Fulltext Index)
- **Database Drivers**: neo4j 5.15.0, psycopg2-binary 2.9.10, elasticsearch 8.18.1

### AI 모델 스택 (AI/ML Models)
- **LLM**: Google Gemini 2.5 (Flash/Pro), OpenAI GPT-4o
- **임베딩 모델**: bge-m3-ko
- **리랭킹 모델**: bge-reranker-v2-m3-ko, rerank-v3.5, Qwen3-Reranker-0.6B

### 데이터 파이프라인 (Data Ingestion)
- **크롤러**: Python 3.10 기반 KAMIS API 크롤러
- **웹 스크래핑**: Playwright 1.49.1
- **문서 처리**: PyPDF (PDF 파싱), WeasyPrint (PDF 생성), Pillow (이미지 처리)

### 인프라 및 DevOps
- **컨테이너화**: Docker 27.5.1, Docker Compose 1.29.2
- **모니터링**: Kibana 8.11.0
- **서버**: Uvicorn 0.35.0 (ASGI)
- **데이터 처리**: Pandas 2.3.1, NumPy 1.26.4, Matplotlib 3.6.3, Seaborn 0.13.2, OpenPyXL 3.1.5
- **유틸리티**: Pydantic 2.11.7, python-dotenv 1.1.1, requests 2.32.4, aiohttp, aiofiles
- **ML 라이브러리**: Transformers 4.54.0, Cohere 5.16.2

### External APIs
- **KAMIS API** (농수산물 가격 정보)
- **arXiv API** (학술 논문)
- **SERPER API** (웹 검색)
- **PubMed API** (의학 문헌)

## 시작하기

### 환경 변수 설정

`.env` 파일을 프로젝트 루트에 생성하고 다음 변수들을 설정하세요:

```bash
# API Keys
OPENAI_API_KEY=your-openai-api-key
GEMINI_API_KEY_1=your-gemini-api-key-1
GEMINI_API_KEY_2=your-gemini-api-key-2
GOOGLE_API_KEY=your-google-api-key
SERPER_API_KEY=your-serper-api-key
LANGSMITH_API_KEY=your-langsmith-api-key

# Database Configuration
POSTGRES_DB=crowdworks_db
POSTGRES_USER=crowdworks_user
POSTGRES_PASSWORD=your-password
POSTGRES_HOST=localhost
POSTGRES_PORT=5433

ELASTICSEARCH_HOST=http://localhost:9200
ELASTICSEARCH_USER=elastic
ELASTICSEARCH_PASSWORD=changeme

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-neo4j-password

# Frontend Configuration
NEXT_PUBLIC_API_URL=http://localhost:8000

# AI Agent Workflow Configuration (선택)
USE_LANGGRAPH=false  # true: LangGraph 워크플로우 사용 (실험 버전), false: 커스텀 멀티에이전트 엔진 사용 (기본)
```

**AI 워크플로우 선택:**
- `USE_LANGGRAPH=false` (기본, 권장): 자체 개발한 안정적인 커스텀 멀티에이전트 엔진 사용
- `USE_LANGGRAPH=true` (실험): LangGraph 기반 워크플로우 사용 (스트리밍 안정성 이슈 존재)

### Docker Compose로 실행

```bash
# 전체 시스템 시작
docker-compose up -d

# 로그 확인
docker-compose logs -f

# 시스템 종료
docker-compose down
```

### 개별 서비스 실행

**Backend (FastAPI)**
```bash
cd multiagent-rag-system/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend (Next.js)**
```bash
cd multiagent-rag-system/frontend
npm install
npm run dev
```

**Crawler**
```bash
cd crawler_rdb
python main.py
```

## 성능 평가 및 벤치마크

### 1. 검색 시스템 성능 평가

#### 실험 조건
- **테스트 데이터**: Multi-Hop(2~4) 200개 쿼리
- **비교 항목**: Pre-check, Graph-to-vector 적용 여부
- **평가 지표**:
  - 검색 품질: nDCG@3 (0: 관련없음 ~ 3: 매우 관련됨)
  - 검색 효율성: 쿼리 분해에 따른 검색 Hops(steps) 및 재검색률
  - 처리 속도: 검색에서 보고서 구조 생성까지 소요 시간(초)

#### Graph Pre-Check 도입 효과 (Graph-to-Vector ON 기준)

**검색 속도**
- Pre-check OFF: 평균 44.79초
- Pre-check ON: 평균 38.85초
- **개선율: 22.6% 속도 향상**

**검색 정확도 (nDCG@3)**
- **5.8% 향상**

**검색 효율성**
- 평균 Hops(steps): 2.2 → 2.4로 증가 (체계적인 Query Decompose 수행)
- 재검색률: 54% → 38%로 감소 (16%p 감소)

#### 하이브리드 검색 시스템 성능 비교

| Method | Avg Response Time | Search Count | Plan Steps | Re-search Rate | nDCG@3 |
|--------|-------------------|--------------|------------|----------------|--------|
| Graph-to-Vector ON + Pre-check OFF | 44.79s | 7.2 | 2.2 | 54.0% | N/A |
| Graph-to-Vector OFF + Pre-check OFF | 31.92s | 7.2 | 2.1 | 40.0% | 0.310603 |
| Graph-to-Vector OFF + Pre-check ON | 41.25s | 7.3 | 2.4 | 44.0% | 0.328667 |
| **Graph-to-Vector ON + Pre-check ON** | **38.85s** | **7.1** | **2.4** | **38.0%** | **0.340703** |

**최적 구성 (Graph-to-Vector ON + Pre-check ON)**
- 가장 높은 검색 정확도 (nDCG@3: 0.340703)
- 가장 낮은 재검색률 (38.0%)
- 효율적인 검색 속도 (38.85초)
- 체계적인 쿼리 분해 (평균 2.4 steps)

### 2. 보고서 품질 평가 (Report Quality Evaluation)

Multi-Agent RAG 시스템이 생성하는 보고서의 품질을 객관적이고 신뢰할 수 있는 방식으로 측정하기 위해 정량적, 정성적 평가를 통합하여 수행합니다. 평가는 **3개의 모델(Gemini 2.5 Flash, Claude Haiku 4.5, GPT-4o)을 활용한 3-Model Ensemble AI Judge** 방식을 채택하여, 단일 모델 평가의 편향성을 제거하고 평가 결과의 신뢰도를 향상시킵니다.

#### 평가 지표 정의

시스템 성능은 **6개의 핵심 지표**를 기준으로 종합 점수(10점 만점)를 산출합니다. 평가는 자동화된 정량 평가와 AI 앙상블 심판을 통한 정성 평가를 병행하며, 각 지표의 중요도에 따라 가중치를 부여합니다.

| 지표 | 가중치 | 측정 방식 | 핵심 측정 항목 |
|------|--------|-----------|----------------|
| **작업 성공률** | 25% | 자동 평가 | 사용자의 요구사항(필수 섹션, 형식)을 성공적으로 완수했는지 측정 |
| **출력 품질** | 25% | AI Judge | 보고서의 사실 정확도, 논리적 일관성, 요구사항 부합도를 종합 평가 |
| **완성도** | 20% | 자동 평가 | 보고서의 구조적 완성도 및 기대 스키마(팀 타입별) 충족 여부 검증 |
| **환각 점수** | 15% | AI Judge | 출처에 근거하지 않은 정보, 왜곡, 과장(환각)이 없는지 측정 (역점수) |
| **효율성** | 10% | 자동 평가 | 생성 시간, 토큰 사용량, 비용, 중복 단계 발생 등 리소스 효율성 평가 |
| **출처 품질** | 5% | 자동 평가 | 인용된 출처의 신뢰도(관련성 점수)와 출처의 다양성 평가 |

**종합 점수 계산식**: `종합 점수 = Σ(각 지표 점수 × 가중치)`

#### 평가 방법

**1. 3-Model Ensemble AI Judge (정성 평가)**
- **평가 지표**: 출력 품질 및 환각 점수 (미묘한 맥락 이해 필요)
- **사용 모델**: Gemini 2.5 Flash, Claude Haiku 4.5, GPT-4o
- **평가 방식**: 3개 모델이 동일한 평가 기준을 바탕으로 독립적으로 보고서를 평가하여 점수를 부여
- **점수 집계**:
  - 기본: 3개 모델의 점수를 가중 평균(Gemini 34%, Claude 33%, GPT 33%)
  - 불일치 처리: 모델 간 점수 차이가 3점 이상 발생 시 중앙값(Median) 채택
  - 환각 평가: 환각 건수는 중앙값, 인용 정확도는 최소값 채택(보수적 평가)

**2. 자동 평가 (정량 평가)**
- **평가 대상**: 작업 성공률, 완성도, 효율성, 출처 품질
- **평가 방식**:
  - **작업 성공률**: 보고서 텍스트와 기대 요구사항 비교
  - **완성도**: 마크다운 헤더 파싱 + Sentence-Transformers 모델로 의미론적 유사도 측정
  - **효율성**: 시스템 로그 분석 (실행 시간 60초 초과, 토큰 50,000개 초과 시 감점)
  - **출처 품질**: 출처별 관련성 점수 평균 + 출처 유형 다양성

#### 평가 데이터셋

실제 업무 환경을 반영한 **5가지 팀 타입(직무)별** 특화된 벤치마크 쿼리로 구성됩니다.

| 팀 타입 | 벤치마크 쿼리 예시 |
|---------|-------------------|
| **구매 담당자** | "국내 농산물 가격 변동 추이 및 구매 최적화 전략", "글로벌 식자재 공급망 리스크 관리", "친환경 유기농 식재료 소싱 가이드" |
| **급식 운영 담당자** | "사내 급식 메뉴 다양화 및 영양 균형 개선", "계절별 식자재 원가 절감 전략", "직원 만족도 향상을 위한 급식 운영 개선" |
| **마케팅 담당자** | "2024년 대체육 시장 동향 및 소비자 선호도", "밀키트 산업 성장 전략", "MZ세대 타겟 건강기능식품 시장 진입 전략" |
| **제품 개발 연구원** | "기능성 식품 원료 트렌드 및 신소재 연구", "식물성 단백질 기반 대체육 제품 개발", "프로바이오틱스 효능 연구 및 제품 적용" |
| **기본(일반)** | "식품 안전 규제 동향 및 컴플라이언스", "푸드테크 산업의 AI 활용 사례", "탄소중립을 위한 ESG 경영 전략" |

**총 평가 쿼리**: 5개 팀 타입 × 3개 쿼리 = **15개**

#### 종합 평가 결과

| 메트릭 | 측정값 | 비고 |
|--------|--------|------|
| **종합 평균 점수** | 8.60/10 | B 등급 |
| **평균 실행 시간** | 96.80초 | 일부 쿼리에서 60초 초과 |
| **등급 분포** | A: 6.7%, B: 86.7%, C: 6.7% | 대부분 B 등급에 집중 |

#### 핵심 지표별 상세 분석

| 지표 | 평균 점수 | 주요 발견사항 |
|------|-----------|---------------|
| **작업 성공률** | 10.0/10 | 15개 쿼리 모두 요구사항 100% 충족 |
| **완성도** | 9.78/10 | Sentence-Transformers 기반 스키마 완성도 측정 효과적 |
| **출력 품질** | 8.23/10 | AI Judge 평가 (사실 정확도, 논리성, 부합도) |
| **환각 점수** | 평균 3.4건 | 주로 '인용 부정확성' (Citation Inaccuracy), 근거 없는 주장은 적음 |
| **효율성** | 6.67/10 | 평균 96.80초로 개선 필요 (목표: 60초 이내) |
| **출처 품질** | 우수 | 평균 27.5개 출처, 다양한 검색 도구 활용 |

#### 팀 타입별 성능 비교

| 순위 | 팀 타입 | 평균 점수 | 특징 |
|------|---------|-----------|------|
| 1 | 제품 개발 연구원 | 8.80/10 | 기술 트렌드, 연구 동향 등 명확한 정보 검색에 강점 |
| 2 | 구매 담당자 | 8.60/10 | 가격 비교로 인한 다수 검색 단계, 효율성 6.5점 |
| 3 | 급식 운영 담당자 | 8.36/10 | 상대적으로 낮지만 여전히 8점 이상 안정적 성능 |

#### 주요 성과 및 개선 방향

**강점 (Strengths)**
- **매우 높은 작업 성공률**: 15/15개 쿼리 모두 누락 없이 완수 (10.0점)
- **안정적인 B등급 성능**: 전체의 86.7%가 8.0~8.9점에 분포, 일관된 품질
- **낮은 심각한 환각 위험**: '근거 없는 주장'보다 '인용 부정확성'이 주를 이룸

**개선 방향 (Next Steps)**
- **환각(인용 부정확성) 개선**: RAG 파이프라인 마지막 단계에서 인용 태그와 출처 내용 교차 검증(Cross-Verify) 로직 추가
- **효율성 최적화**: 불필요한 중복 검색 단계 제거, LLM 호출 병렬화로 실행 시간 60초 이내 단축 목표

상세 평가 보고서는 [docs/evaluation/PERFORMANCE_EVALUATION_REPORT.md](docs/evaluation/PERFORMANCE_EVALUATION_REPORT.md)를 참고하세요.

## 팀 구성 및 역할

| 김민재 (팀장)                                                                                  | 강민선                                                                                         |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| <img src="https://avatars.githubusercontent.com/kmj200392" width="160px" alt="Minjae Kim" />  | <img src="https://avatars.githubusercontent.com/KangMinSun" width="160px" alt="Minsun Kang" /> |
| [GitHub: @kmj200392](https://github.com/kmj200392)                                            | [GitHub: @KangMinSun](https://github.com/KangMinSun)                                          |
| 데이터 전처리 및 임베딩 설계<br>Vector RAG 구축                                               | 크롤러 및 데이터 수집 파이프라인 개발<br>페르소나 관리체계 설계 및 Prompt Engineering        |
| 고려대학교 컴퓨터학과                                                                          | 고려대학교 컴퓨터학과                                                                          |

| 김희은                                                                                        | 이동영                                                                                          |
| --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| <img src="https://avatars.githubusercontent.com/heeeun-kim" width="160px" alt="Heeun Kim" /> | <img src="https://avatars.githubusercontent.com/GBEdge01" width="160px" alt="Dongyoung Lee" /> |
| [GitHub: @heeeun-kim](https://github.com/heeeun-kim)                                         | [GitHub: @GBEdge01](https://github.com/GBEdge01)                                               |
| Graph RAG 구축<br>성능 실험 및 평가 시스템 구축                                              | 크롤러 및 데이터 수집 파이프라인 개발<br>Vector RAG 구축                                       |
| 고려대학교 컴퓨터학과                                                                         | 고려대학교 컴퓨터학과                                                                           |

| 이성민                                                                                          |
| ----------------------------------------------------------------------------------------------- |
| <img src="https://avatars.githubusercontent.com/danlee-dev" width="160px" alt="Seongmin Lee" /> |
| [GitHub: @danlee-dev](https://github.com/danlee-dev)                                            |
| Agentic RAG System 개발<br>FastAPI 서버 구축 및 웹 프론트엔드 개발<br>문서 관리 및 형상 관리   |
| 고려대학교 컴퓨터학과                                                                           |

## 협력 기업

**크라우드웍스 (CrowdWorks)**

본 프로젝트는 크라우드웍스의 AI 전처리 솔루션 Alpy를 활용하여 개발되었습니다.

- 웹사이트: https://www.crowdworks.ai
- Alpy 소개: https://www.crowdworks.ai/agent/alpykc

## 라이선스

본 프로젝트는 고려대학교 산학협력 프로젝트로 개발되었습니다.

## 문의

프로젝트 관련 문의사항은 GitHub Issues를 통해 남겨주시기 바랍니다.

## 참고 자료

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Neo4j Documentation](https://neo4j.com/docs/)
- [Elasticsearch Documentation](https://www.elastic.co/guide/index.html)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Next.js Documentation](https://nextjs.org/docs)

---

## Project Tech Stack

### Environment
![Visual Studio Code](https://img.shields.io/badge/Visual%20Studio%20Code-007ACC?style=for-the-badge&logo=visualstudiocode&logoColor=white)
![Git](https://img.shields.io/badge/Git-F05032?style=for-the-badge&logo=git&logoColor=white)
![GitHub](https://img.shields.io/badge/GitHub-181717?style=for-the-badge&logo=github&logoColor=white)

### Backend & AI
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-121212?style=for-the-badge&logo=chainlink&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?style=for-the-badge&logo=graphql&logoColor=white)

### Frontend
![Next.js](https://img.shields.io/badge/Next.js-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)
![React](https://img.shields.io/badge/React-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Tailwind CSS](https://img.shields.io/badge/Tailwind%20CSS-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)
![Chart.js](https://img.shields.io/badge/Chart.js-FF6384?style=for-the-badge&logo=chartdotjs&logoColor=white)

### Database
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-008CC1?style=for-the-badge&logo=neo4j&logoColor=white)
![Elasticsearch](https://img.shields.io/badge/Elasticsearch-005571?style=for-the-badge&logo=elasticsearch&logoColor=white)

### Infrastructure & Deployment
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Naver Cloud Platform](https://img.shields.io/badge/Naver%20Cloud%20Platform-03C75A?style=for-the-badge&logo=naver&logoColor=white)
![Kibana](https://img.shields.io/badge/Kibana-005571?style=for-the-badge&logo=kibana&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)

### AI Models & APIs
![Google Gemini](https://img.shields.io/badge/Google%20Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white)
