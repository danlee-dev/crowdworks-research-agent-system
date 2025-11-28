from langchain_core.tools import tool
import re
import sys
import asyncio
import json
import concurrent.futures
import os
from typing import Dict, List, Any, Optional, AsyncGenerator, Tuple

# Fallback 시스템 import (Docker 볼륨 마운트된 utils 폴더)
sys.path.append('/app')
from utils.model_fallback import ModelFallbackManager
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# arxiv_search 함수의 실제 로직을 직접 구현
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from langchain.prompts import PromptTemplate
from langchain import hub
from langchain.agents import AgentExecutor, create_react_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from ..models.models import SearchResult
from ...services.search.search_tools import (
    debug_web_search,
    rdb_search,
    vector_db_search,
    graph_db_search,
    scrape_and_extract_content,
    arxiv_search,
)
from ...utils.session_logger import get_session_logger, set_current_session, session_print

# 전역 ThreadPoolExecutor 생성 (재사용으로 성능 향상)
_global_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="search_worker")

# 추가: 페르소나 프롬프트 로드
PERSONA_PROMPTS = {}
try:
    import os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "prompts", "persona_prompts.json")
    with open(file_path, "r", encoding="utf-8") as f:
        PERSONA_PROMPTS = json.load(f)
    print(f"ProcessorAgent: 페르소나 프롬프트 로드 성공 ({len(PERSONA_PROMPTS)}개).")
except Exception as e:
    print(f"ProcessorAgent: 페르소나 프롬프트 로드 실패 - {e}")


class DataGathererAgent:
    """데이터 수집 및 쿼리 최적화 전담 Agent"""

    def __init__(self, model: str = "gemini-2.5-flash-lite", temperature: float = 0):
        # Gemini 모델 (기본)
        self.llm = ChatGoogleGenerativeAI(model=model, temperature=temperature)

        # Gemini 백업 키와 OpenAI fallback 모델들

        # Gemini 백업 모델 (두 번째 키)
        try:
            self.llm_gemini_backup = ChatGoogleGenerativeAI(
                model=model,
                temperature=temperature,
                google_api_key=ModelFallbackManager.GEMINI_KEY_2
            )
            print("DataGathererAgent: Gemini 백업 모델 (키 2) 초기화 완료")
        except Exception as e:
            print(f"DataGathererAgent: Gemini 백업 모델 초기화 실패: {e}")
            self.llm_gemini_backup = None

        # OpenAI fallback 모델들
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if self.openai_api_key:
            self.llm_openai_mini = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=temperature,
                api_key=self.openai_api_key
            )
            self.llm_openai_4o = ChatOpenAI(
                model="gpt-4o",
                temperature=temperature,
                api_key=self.openai_api_key
            )
            print("DataGathererAgent: OpenAI fallback 모델 초기화 완료")
        else:
            self.llm_openai_mini = None
            self.llm_openai_4o = None
            print("DataGathererAgent: 경고: OPENAI_API_KEY가 설정되지 않음")

        # 도구 매핑 설정 - 이름 통일
        self.tool_mapping = {
            "web_search": self._web_search,
            "vector_db_search": self._vector_db_search,
            "graph_db_search": self._graph_db_search,
            "arxiv_search": self._arxiv_search,
            "pubmed_search": self._pubmed_search,
            "rdb_search": self._rdb_search,
            "scrape_content": self._scrape_content,
        }

    # === [NEW] 그래프 리포트에서 '원산지 관계 정보' 파싱 ===
    def _parse_isfrom_from_graph_report(self, text: str):
        try:
            import re
            if "원산지 관계 정보" not in text:
                return []
            section = text.split("원산지 관계 정보", 1)[1]
            pat = re.compile(
                r"^\s*\d+\.\s+(?P<item>.+?)\s*→\s*(?P<origin>.+?)"
                r"(?:\s*\(농장수\s*(?P<count>\d+)\s*개\))?\s*$",
                re.MULTILINE
            )
            out = []
            for m in pat.finditer(section):
                d = {k: (v.strip() if isinstance(v, str) and v else v) for k, v in m.groupdict().items()}
                if d.get("count"):
                    try: d["count"] = int(d["count"])
                    except: pass
                out.append(d)
            return out
        except Exception:
            return []

    # === [NEW] 그래프 리포트에서 '영양성분 관계 정보' 파싱 ===
    def _parse_nutrients_from_graph_report(self, text: str):
        try:
            import re
            if "영양성분 관계 정보" not in text:
                return []
            section = text.split("영양성분 관계 정보", 1)[1]
            pat = re.compile(
                r"^\s*\d+\.\s+(?P<item>.+?)\s*-\s*(?P<nutrient>.+?)"
                r"(?:\s*\(양:\s*(?P<value>[0-9.]+)\s*(?P<unit>[^)]+)?\))?\s*$",
                re.MULTILINE
            )
            out = []
            for m in pat.finditer(section):
                d = {k: (v.strip() if isinstance(v, str) and v else v) for k, v in m.groupdict().items()}
                if d.get("value"):
                    try: d["value"] = float(d["value"])
                    except: pass
                out.append(d)
            return out
        except Exception:
            return []

    # === [NEW] 그래프 리포트에서 '문서 관계 정보' 파싱 ===
    def _parse_docrels_from_graph_report(self, text: str):
        """
        neo4j_rag_tool의 문자열 리포트에서 '문서 관계 정보' 블록만 추출해
        [{source, target, rel_type?, doc?}, ...] 형태로 반환.
        """
        try:
            import re
            if "문서 관계 정보" not in text:
                return []
            # 해당 섹션만 잘라내기
            section = text.split("문서 관계 정보", 1)[1]
            # 번호목록 라인 파싱: "  1. 소스 - 타겟 (type: 타입)"
            pat = re.compile(
                r"^\s*\d+\.\s+(?P<source>.+?)\s*-\s*(?P<target>.+?)"
                r"(?:\s*\(type:\s*(?P<rel_type>[^)]+)\))?\s*$",
                re.MULTILINE
            )
            out = []
            for m in pat.finditer(section):
                d = {k: (v.strip() if v else v) for k, v in m.groupdict().items()}
                # doc 필드는 Neo4j 출력에 없으므로 None으로 설정
                d["doc"] = None
                out.append(d)
            return out
        except Exception:
            return []

    # === [NEW] 그래프 전체 증거(docrels + isfrom + nutrients)로 벡터 쿼리 한 줄 생성 ===
    async def _refine_query_from_graph_all(
        self,
        original_query: str,
        docrels: List[Dict[str, str]],
        isfrom: List[Dict[str, str]],
        nutrients: List[Dict[str, str]],
    ) -> str:

        preview = {
            "docrels": docrels,
            "isfrom": isfrom,
            "nutrients": nutrients,
        }
        preview_json = json.dumps(preview, ensure_ascii=False)

        prompt = f"""
당신은 Elasticsearch 기반 Vector DB에서 고정밀 검색을 수행하기 위한 **자연스러운 한국어 문장**을 작성하는 도우미입니다.
'사용자 질문'과 '그래프에서 발견된 관계 정보'(docrels, isfrom, nutrients)를 바탕으로, 사용자의 **원래 검색 의도는 보존**하면서
멀티홉에서 확인된 **연결 고리(bridge)** 를 문장 속에 자연스럽게 포함하여, 벡터 검색을 **실질적으로 정밀화**하는 쿼리로 개선하세요.

핵심 원칙:
1) **의도 완전 보존**: 질문의 목적/범위/맥락을 바꾸거나 축소·확장하지 마세요.
2) **브리지 활용 보강**: docrels(연관 아이템/문서 관계), isfrom(원산지/출처), nutrients(영양 성분·수치/단위) 중
   원 질문의 모호성을 줄이거나 대상을 특정해줄 단서를 선별해 자연스럽게 삽입하세요.
3) **Vector DB 최적화**: 문서에서 실제로 쓰일 가능성이 높은 표현·동의어를 사용하되, **키워드 나열이 아닌 완전한 문장**으로 작성하세요.
4) **증거 기반**: 그래프에 없는 정보는 만들지 말고, 수치/단위/출처는 **그래프 값만** 사용하세요.
5) **무효 보강 방지**: 그래프 단서가 검색 품질을 실질적으로 개선하지 못하면 **원 질문을 그대로 출력**하세요.

그래프 증거 활용 가이드:
- 문서관계(docrels): 대상 식품/성분 간의 관계나 참고 문서 유형을 **대상 특정화**에 도움이 될 때만 간결히 반영
- 원산지(isfrom): 지역/국가가 질문 맥락에 의미가 있을 때만 자연스럽게 포함
- 영양소(nutrients): 영양 성분 관련 질문이면 구체적인 영양소명이나 성분 정보 포함

작성 규칙(멀티홉 대응):
- 원 질문의 핵심 표현을 유지하고, 브리지 단서를 **괄호·종속절** 등으로 매끄럽게 결합하여 구체화
- 동의어/약어는 문맥상 자연스럽게 1회만 사용

주의사항:
- **길이 하한**: 최종 문장은 **원 질문 길이 이상**이어야 합니다.
- 그래프 단서가 검색 품질을 개선하지 못한다고 판단되면 **원 질문을 그대로 출력**하세요.
- 사용자가 묻지 않은 새 주제로 확장 금지
- 질문 범위를 임의로 좁히거나 일반화하지 마세요
- 그래프에 없는 값 추정 금지

사용자 질문: "{original_query}"

그래프 증거:
{preview_json}

개선된 Vector 검색 쿼리:


### 예시 A (의도 보존 + 브리지 주입)
원 질문: "성인 계란 일일 권장 섭취량과 주의사항, 영양소 통계 수치 데이터"
그래프(발췌):
{{
  "docrels":[
    {{"source":"계란","target":"식약처_2021_권고","rel_type":"guideline","doc":"mfds_2021.pdf"}},
    {{"source":"계란","target":"콜레스테롤_주의","rel_type":"caution","doc":"who_lipid_guide.pdf"}}
  ],
  "nutrients":[
    {{"item":"계란","nutrient":"단백질","value":12.6,"unit":"g/100g"}},
    {{"item":"계란","nutrient":"콜레스테롤","value":373.0,"unit":"mg/100g"}}
  ],
  "isfrom":[{{"item":"계란","origin":"국내산","count":18}}]
}}
출력(예):
"성인 대상의 계란 섭취에 대해 일일 권장량과 주의사항을 확인할 수 있는 자료(식약처 2021 권고, WHO 지질 가이드)와 함께 단백질(12.6 g/100g), 콜레스테롤(373 mg/100g) 등 영양소 수치 통계를 다룬 문서를 찾아줘."

### 예시 B (증거가 도움 안 되면 원문 유지)
원 질문: "아보카도 원산지별 수입 비중 통계"
그래프(발췌): {{"nutrients":[{{"item":"아보카도","nutrient":"비타민E","value":2.1,"unit":"mg/100g"}}]}}
출력(예):
"아보카도 원산지별 수입 비중 통계"

### 예시 C (isfrom로 모호성 축소)
원 질문: "사과 품종별 당도 비교 통계"
그래프(발췌): {{"isfrom":[{{"item":"후지","origin":"일본산","count":12}},{{"item":"홍옥","origin":"국내산","count":7}}]}}
출력(예):
"사과 품종별 당도 비교 통계를 찾되 후지(일본산)와 홍옥(국내산)에 대한 수치 비교가 포함된 문서를 중심으로 검색해줘."

### 예시 D (경고 문서 + 수치 결합)
원 질문: "참치캔 나트륨 1회 제공량 기준과 건강상 주의사항"
그래프(발췌):
{{
  "docrels":[{{"source":"참치캔","target":"나트륨_주의","rel_type":"caution","doc":"sodium_alert_v2.pdf"}}],
  "nutrients":[{{"item":"참치캔","nutrient":"나트륨","value":320.0,"unit":"mg/serving"}}]
}}
출력(예):
"참치캔 섭취와 관련해 1회 제공량 기준의 나트륨 수치(320 mg/serving)와 건강상 주의사항을 제시한 경고·가이드 문서를 중심으로 찾아줘."
""".strip()

        try:
            refined = await self._invoke_with_fallback(prompt)
            refined = refined.strip().splitlines()[0].strip()
            return refined or original_query
        except Exception:
            return original_query
        
    # === [NEW] 그래프 증거를 질문과의 직접 관련성 기준으로 선별(LLM) ===
    async def _select_relevant_graph_evidence(
        self,
        original_query: str,
        docrels: List[dict],
        isfrom: List[dict],
        nutrients: List[dict],
        k_per_type: int = 8,
    ) -> Dict[str, List[dict]]:
        """
        입력된 그래프 증거들을 한 번에 LLM에 넘겨 '질문과 직접 관련' 항목만 선별.
        실패 시에는 상위 k개 슬라이스로 폴백.
        반환 형식: {"docrels":[...], "isfrom":[...], "nutrients":[...]}
        """
        import json

        def _head(xs, n=8):
            return xs[:n] if xs else []

        # LLM이 처리하기 쉽게 미리 자른 preview(과대 입력 방지)
        preview = {
            "docrels": _head(docrels, 50),
            "isfrom": _head(isfrom, 50),
            "nutrients": _head(nutrients, 50),
        }
        preview_json = json.dumps(preview, ensure_ascii=False)

        prompt = f"""
당신은 랭킹/필터링 모델입니다. '사용자 질문'과 '그래프 증거 후보'를 보고,
질문과 **직접적으로 관련**되거나, 멀티홉 탐색에서 **연결 고리(bridge)** 로 작동해 벡터 재검색 정확도를 올릴 항목만 타입별 최대 {k_per_type}개씩 골라서 JSON으로만 응답하세요.

반드시 아래 스키마를 지키세요(존재하는 키만 유지; 값은 원본에서 그대로 복사):
{{
  "docrels":   [{{"source": str, "target": str, "rel_type": str, "doc": str}}] ,
  "isfrom":    [{{"item": str, "origin": str, "count": int|null}}] ,
  "nutrients": [{{"item": str, "nutrient": str, "value": float|null, "unit": str|null}}]
}}

선정 목표(멀티홉):
- 질문의 핵심 엔티티/조건과 **직접 연결(1-hop)** 되거나,
- 질문의 엔티티 ↔ 목표 정보 사이의 경로를 **최대 2-hop 이내로 닫아주는 브리지**만 선택하세요.
  (예: docrels로 연결된 아이템 → 해당 아이템의 isfrom/영양소로 이어지는 경로)

선정 기준(우선순위):
1) 직접 연결(1-hop) > 브리지(2-hop)
2) 다수 후보에 **공통으로 연결**되는 항목, isfrom.count가 큰 항목 우선
3) 구체 명칭/원산지/관계타입이 **모호어/일반어**보다 우선

제외 규칙:
- 질문의 의도와 **경로가 닫히지 않는** 항목
- 중복/동의어/경미한 표기 차이 → 하나만 남기기
- 서로 **모순**되는 값이 동시에 존재할 경우 신뢰도가 낮은 것 제거
- 단지 연상만 가능한 항목(주제 확장) 금지

최소-충분 집합:
- 질문의 주요 제약(대상·속성·조건)을 **모두 커버**하는 **최소 수의 항목**만 남기세요.
- 기준을 충족하는 것이 없으면 해당 타입 배열은 **빈 배열**로 두세요.

형식 제약:
- JSON만 출력(여는/닫는 중괄호 포함). 앞뒤 설명·코드블록 금지.
- 미존재 타입 키는 생략 가능. 값은 **원본에서 그대로 복사**.

사용자 질문: "{original_query}"

그래프 증거 후보(JSON):
{preview_json}
""".strip()

        try:
            txt = await self._invoke_with_fallback(prompt)  # 기존 LLM 호출 유틸을 재사용
            # JSON만 추출
            m = re.search(r"\{[\s\S]*\}\s*$", txt.strip())
            if m:
                txt = m.group(0)
            data = json.loads(txt)
            # 형식 보정 및 슬라이스
            sel_docrels   = _head(list(data.get("docrels", []) or []),   k_per_type)
            sel_isfrom    = _head(list(data.get("isfrom", []) or []),    k_per_type)
            sel_nutrients = _head(list(data.get("nutrients", []) or []), k_per_type)
            return {"docrels": sel_docrels, "isfrom": sel_isfrom, "nutrients": sel_nutrients}
        except Exception as e:
            print(f"  - [WARN] LLM evidence selection failed: {e} (fallback to head)")
            return {"docrels": _head(docrels, k_per_type),
                    "isfrom": _head(isfrom, k_per_type),
                    "nutrients": _head(nutrients, k_per_type)}


    async def _invoke_with_fallback(self, prompt: str, use_4o: bool = False) -> str:
        """Gemini API 실패 시 OpenAI로 fallback하는 메서드"""
        try:
            # 1차 시도: Gemini (키 1)
            print("  - Gemini 키 1 API 시도 중...")
            response = await self.llm.ainvoke(prompt)
            return response.content.strip()
        except Exception as e:
            error_msg = str(e)
            print(f"  - Gemini 키 1 API 실패: {error_msg}")

            # Rate limit 또는 quota 오류 체크
            if any(keyword in error_msg.lower() for keyword in ['429', 'quota', 'rate limit', 'exceeded']):
                print("  - Rate limit 감지, fallback 순차 시도...")

                # 2차 시도: Gemini 백업 (키 2)
                if self.llm_gemini_backup:
                    try:
                        print("  - Gemini 키 2 API 시도 중...")
                        response = await self.llm_gemini_backup.ainvoke(prompt)
                        print("  - Gemini 키 2 API 성공!")
                        return response.content.strip()
                    except Exception as backup_error:
                        print(f"  - Gemini 키 2 API도 실패: {backup_error}")

                # 3차 시도: OpenAI
                if self.llm_openai_mini:
                    try:
                        fallback_model = self.llm_openai_4o if use_4o else self.llm_openai_mini
                        model_name = "gpt-4o" if use_4o else "gpt-4o-mini"
                        print(f"  - {model_name} API 시도 중...")
                        response = await fallback_model.ainvoke(prompt)
                        print(f"  - {model_name} API 성공!")
                        return response.content.strip()
                    except Exception as openai_error:
                        print(f"  - OpenAI API도 실패: {openai_error}")
                        raise openai_error
                else:
                    print("  - OpenAI API 키가 없어 최종 fallback 불가")
                    raise e
            else:
                # Rate limit이 아닌 다른 오류는 그대로 발생
                raise e

    async def _optimize_query_for_tool(self, query: str, tool: str) -> str:
        """각 도구의 특성에 맞게 자연어 쿼리를 최적화합니다."""

        # RDB와 GraphDB는 키워드 기반 검색에 더 효과적입니다.
        if tool == "rdb_search":
            print(f"  - {tool} 쿼리 최적화 시작: '{query}'")
            prompt = f"""
        너는 PostgreSQL RDB에 질의할 검색문을 만드는 어시스턴트다.
        사용자 질문을 RDB에서 실제로 검색 가능한 형태로 재작성해라.

        [중요: RDB 테이블 구조 이해]
        - 가격/시세 테이블: kamis_product_price_latest (품목명: 사과, 배, 감귤, 쌀, 고등어, 전복 등 구체적 품목)
        - 영양 테이블: foods + proximates/minerals/vitamins (식품명: 사과, 쌀, 고등어 등 구체적 식품)

        [품목 변환 규칙 - 매우 중요]
        1) 추상적 카테고리 → 구체적 품목들로 변환:
        - "농축수산물" → "사과, 배, 쌀, 감자"
        - "특산품" → "감귤, 사과, 배"
        - "농산물" → "사과, 배, 쌀, 감자"
        - "수산물" → "고등어, 명태" (KAMIS에 제한적)
        - "건강기능식품" → "홍삼, 프로폴리스"

        2) 구체적 품목은 그대로 유지:
        - "사과" → "사과"
        - "감귤" → "감귤"
        - "쌀" → "쌀"

        [의도 단순화]
        - 복잡한 표현 → 단순 키워드:
        - "시세 변동 추이" → "가격"
        - "영양성분(비타민·미네랄·단백질)" → "영양성분"
        - "시장 현황 분석" → "시장현황"

        [기간 정규화]
        - "2025년 7-8월" → "recent"
        - "최근" → "recent"
        - "현재" → "today"

        [출력 형식]
        구체적 품목명을 포함한 자연스러운 한국어 문장으로 작성:

        [변환 예시]
        - 입력: "지역=대한민국, 품목=농축수산물, 의도=시세 변동 추이, 기간=2025년 7-8월"
        - 출력: "사과 배 쌀 감자 최근 가격 정보"

        - 입력: "지역=대한민국, 품목=특산품, 의도=시세 변동 추이, 기간=7-8월"
        - 출력: "감귤 사과 배 가격 거래량 정보"

        - 입력: "건강기능식품 시장 동향 분석"
        - 출력: "홍삼 프로폴리스 시장현황"

        사용자 질문: "{query}"
        최적화된 검색문:
        """
            try:
                response_content = await self._invoke_with_fallback(prompt)
                optimized_query = response_content.strip()
                print(f"  - 최적화된 키워드: '{optimized_query}'")
                return optimized_query
            except Exception as e:
                print(f"  - 쿼리 최적화 실패, 원본 쿼리 사용: {e}")
                return query

        elif tool == "graph_db_search":
            print(f"  - {tool} 쿼리 최적화 시작: '{query}'")

            # 그래프 스키마 기준: (품목)-[:isFrom]->(Origin), (품목)-[:hasNutrient]->(Nutrient)
            # 우리가 원하는 정규 문구:
            #   - "<품목>의 원산지"            -> isFrom
            #   - "<품목>의 영양소"            -> hasNutrient
            #   - "<지역>의 <품목> 원산지"     -> isFrom + region filter
            #   - "(활어|선어|냉동|건어) <수산물> 원산지" -> isFrom + fishState filter
            prompt = f"""
        다음 사용자 질문을, Neo4j 그래프 검색에 바로 넣을 수 있는 **정규 질의 문구**로 변환하세요.
        그래프 스키마:
        - 품목 노드 라벨: Ingredient, Food  (공통 속성: product)
        - 원산지 노드 라벨: Origin (속성: city, region)
        - 영양소 노드 라벨: Nutrient (속성: name)
        - 문서 내 키워드 노드 라벨: Entity (속성: name)
        - 관계: (Ingredient)-[:isFrom]->(Origin), (Food)-[:hasNutrient]->(Nutrient), (Entity)-[:relation]->(Entity)

        규칙(반드시 준수):
        1) 결과는 **한 줄당 하나의 질의**로 출력하고, 아래 4가지 패턴만 사용하세요.
        - "<품목>의 원산지"
        - "<품목>의 영양소"
        - "<지역>의 특산품"
        2) 질문에 해당되지 않는 패턴은 만들지 마세요. 추측 금지.
        3) 불필요한 접두사/설명/따옴표/번호/Bullet 금지. 텍스트만.
        4) 결과가 없으면 **빈 문자열**만 반환.

        예시:
        - 질문: "사과 어디서 나와?"  ->  사과의 원산지
        - 질문: "오렌지 영양 성분 알려줘" -> 오렌지의 영양소
        - 질문: "경상북도에서는 뭐가 생산돼?" -> 경상북도의 특산품
        - 질문: "이번 호우로 충청도 농산물 피해가 심각해." -> 충청도의 특산품

        질문: "{query}"
        출력:
        """.strip()

            try:
                response_content = await self._invoke_with_fallback(prompt)
                # 파싱: 줄 단위로 정리, 허용 패턴만 통과
                allowed_prefixes = ("활어 ", "선어 ", "냉동 ", "건어 ")
                def _is_allowed(line: str) -> bool:
                    if not line: return False
                    # 패턴 1/2: "<품목>의 (원산지|영양소)"
                    if "의 원산지" in line or "의 영양소" in line:
                        return True
                    # 패턴 3: "<지역>의 <품목> 원산지"
                    if line.endswith(" 원산지") and "의 " in line and not any(line.startswith(p) for p in allowed_prefixes):
                        return True
                    # 패턴 4: "<상태> <수산물> 원산지"
                    if line.endswith(" 원산지") and any(line.startswith(p) for p in allowed_prefixes):
                        return True
                    return False

                lines = [l.strip().lstrip("-•").strip().strip('"').strip("'") for l in response_content.splitlines()]
                lines = [l for l in lines if _is_allowed(l)]
                optimized_query = "\n".join(dict.fromkeys(lines))  # 중복 제거, 순서 유지

                print(f"  - 최적화된 키워드(정규 질의):\n{optimized_query or '(empty)'}")
                return optimized_query if optimized_query else query  # 비면 원본 질문 전달

            except Exception as e:
                print(f"  - 쿼리 최적화 실패, 원본 쿼리 사용: {e}")
                return query

        # Vector DB는 구체적인 정보 검색 질문으로 변환
        elif tool == "vector_db_search":
            print(f"  - {tool} 쿼리 최적화 시작 (수치/표/통계 키워드 특화): '{query}'")
            prompt = f"""
다음 요청을 Vector DB에서 **수치 데이터, 표, 통계 자료**를 효과적으로 검색할 수 있는 **키워드 조합**으로 변환해주세요.

**중요 규칙 (반드시 준수)**:
1. **질문 형식 금지** - "~인가요?", "~입니까?" 등 질문 표현 사용 금지
2. **키워드 중심의 검색어만 생성** - 명사와 핵심 키워드만 조합
3. **번호 매기기 절대 금지** (1., 2., 3. 등 사용 금지)
4. **부가 설명 금지** - "원본 요청:", "검색어:" 등 불필요한 텍스트 제거
5. **한 줄의 키워드 조합만 출력**

**수치/표/통계 데이터 특화 키워드 규칙**:
- **수치 데이터 키워드**: 매출액, 시장규모, 점유율, 생산량, 소비량, 수출입량, 가격, 순위, 비율, 통계
- **표/차트 키워드**: 표, 통계표, 현황표, 데이터, 차트, 그래프, 도표
- **비교 분석 키워드**: 지역별, 업체별, 브랜드별, 연도별, 월별, 상위, 순위, 비교
- **시계열 키워드**: 2024년, 2025년, 7월, 8월, 분기별, 월별, 년도별, 변화율, 증감
- **정량 지표**: 억원, 조원, 퍼센트, 톤, 개, 명, 건수, 증가율, 감소율

변환 예시 (키워드 중심):
입력: "국내 건강기능식품 시장의 최근 트렌드를 분석합니다"
출력: 2024년 대한민국 건강기능식품 시장규모 매출액 기업별 점유율 순위 통계표

입력: "우리나라 식자재 시장의 인사이트를 알려주세요"
출력: 2024년 대한민국 식자재 시장규모 통계 지역별 생산량 수치 데이터

입력: "집중호우 피해지역 농산물 현황을 조사합니다"
출력: 2025년 7월 8월 집중호우 피해지역 농산물 생산량 감소율 피해액 통계표

입력: "만두 시장 분석을 합니다"
출력: 2024년 대한민국 만두 시장규모 브랜드별 점유율 순위 통계 매출액

입력: "식품 가격 동향을 파악합니다"
출력: 2024년 2025년 식품 품목별 가격 변동률 월별 물가지수 통계표

**원본 요청**: "{query}"

**변환된 키워드 조합** (키워드만 출력):
"""
            try:
                raw_query = await self._invoke_with_fallback(prompt)

                # [수정됨] 키워드 조합 추출
                optimized_query = self._extract_keywords(raw_query)

                print(f"  - 최적화된 키워드: '{optimized_query}'")
                return optimized_query
            except Exception as e:
                print(f"  - 쿼리 최적화 실패, 원본 쿼리 사용: {e}")
                return query

        # Web Search는 맥락 정보를 포함한 검색 키워드로 최적화
        elif tool == "web_search":
            print(f"  - {tool} 쿼리 최적화 시작 (맥락 강화): '{query}'")
            prompt = f"""
다음 질문을 웹 검색에 최적화된 키워드로 변환해주세요.
검색 효과를 높이기 위해 중요한 맥락 정보(국가, 연도, 대상 등)를 포함해야 합니다.

최적화 규칙:
1. 지역 정보 명확화 및 기본값 설정:
   - "국내" → "대한민국"
   - "우리나라" → "대한민국"
   - "한국" → "대한민국"
   - 지역 언급이 없으면 → "대한민국" 자동 추가
2. 구체적인 연도 명시:
   - "최근" → "2024년 2025년"
   - "요즘" → "2024년 2025년"
   - "현재" → "2025년"
   - "작년" → "2024년"
3. 구체적인 분야나 대상 명시
4. 검색 의도에 맞는 키워드 조합

예시:
- 원본: "국내 건강기능식품 시장 현황을 조사합니다"
- 최적화: "2024년 2025년 대한민국 건강기능식품 시장 현황 트렌드"

- 원본: "우리나라 MZ세대 소비 패턴을 분석합니다"
- 최적화: "2024년 2025년 대한민국 MZ세대 소비 트렌드 패턴"

- 원본: "건강기능식품 시장 현황을 조사합니다" (지역 언급 없음)
- 최적화: 2024년 2025년 대한민국 건강기능식품 시장 현황 트렌드

- 원본: "한국의 유망한 건강기능식품 분야를 추천합니다"
- 최적화: 2024년 2025년 대한민국 건강기능식품 유망 분야 시장 전망

- 원본: "최근 식자재 시장 트렌드를 조사합니다"
- 최적화: 2024년 2025년 대한민국 식자재 시장 트렌드 동향 분석

중요: 큰따옴표나 작은따옴표를 사용하지 마세요. 자연스러운 검색어로 작성하세요.

원본 질문: "{query}"
최적화된 검색 키워드 (따옴표 없이):
"""
            try:
                optimized_query = await self._invoke_with_fallback(prompt)
                # 따옴표 제거 (혹시 LLM이 추가했을 경우를 대비)
                optimized_query = optimized_query.strip().strip('"').strip("'")
                print(f"  - 최적화된 검색 키워드: '{optimized_query}'")
                return optimized_query
            except Exception as e:
                print(f"  - 쿼리 최적화 실패, 원본 쿼리 사용: {e}")
                return query

        elif tool == "pubmed_search":
            print(f"  - {tool} 쿼리 최적화 시작 (맥락 강화): '{query}'")
            prompt = f"""
다음 질의에서 PubMed에 검색할 키워드를 5단어 이하로 추출하고 **영어**로 번역해주세요.
영어로 번역된 검색 키워드를 제외한 다른 내용은 출력하지 마세요.

입력: {query}
출력(영어 키워드, 따옴표 없이):
"""
            try:
                optimized_query = await self._invoke_with_fallback(prompt)
                # 따옴표 제거 (혹시 LLM이 추가했을 경우를 대비)
                optimized_query = optimized_query.strip().strip('"').strip("'")
                print(f"  - 최적화된 검색 키워드: '{optimized_query}'")
                return optimized_query
            except Exception as e:
                print(f"  - 쿼리 최적화 실패, 원본 쿼리 사용: {e}")
                return query

        # 기타 도구는 원본 쿼리 그대로 사용
        return query

    def _extract_keywords(self, raw_response: str) -> str:
        """LLM 응답에서 키워드 조합만 추출하는 헬퍼 메서드"""
        lines = raw_response.strip().split('\n')

        # 불필요한 텍스트 제거
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 번호 매기기, 설명, 질문 형태 제거
            if line.startswith(('1.', '2.', '3.', '4.', '5.', '6.', '-', '*')):
                continue
            if any(word in line for word in ['원본', '변환', '키워드:', '출력:', '검색어:']):
                continue
            if '**' in line:  # 마크다운 헤더 제거
                continue
            if line.endswith(('?', '인가요', '입니까', '나요')):  # 질문 형태 제거
                continue
            cleaned_lines.append(line)

        # 첫 번째 키워드 조합만 반환
        for line in cleaned_lines:
            # 키워드가 포함된 라인 찾기
            if any(keyword in line for keyword in ['시장', '통계', '데이터', '매출', '생산', '가격', '점유율']):
                return line

        # fallback: 첫 번째 의미있는 라인
        if cleaned_lines:
            return cleaned_lines[0]

        return raw_response.strip()

    def _extract_single_question(self, raw_response: str) -> str:
        """LLM 응답에서 단일 질문만 추출하는 헬퍼 메서드 (기존 함수 유지)"""
        lines = raw_response.strip().split('\n')

        # 불필요한 텍스트 제거
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 번호 매기기나 부가 설명 제거
            if line.startswith(('1.', '2.', '3.', '4.', '5.', '6.', '-', '*')):
                continue
            if '원본' in line or '변환' in line or '질문:' in line or '출력:' in line:
                continue
            if '**' in line:  # 마크다운 헤더 제거
                continue
            cleaned_lines.append(line)

        # 첫 번째 질문 문장만 반환
        for line in cleaned_lines:
            if line.endswith('?') or line.endswith('요') or line.endswith('까'):
                return line

        # 적절한 질문이 없으면 첫 번째 유효한 라인 반환
        if cleaned_lines:
            return cleaned_lines[0]

        # 모든 처리가 실패하면 원본 응답 반환
        return raw_response.strip()


    async def execute(self, tool: str, inputs: Dict[str, Any], state: Dict[str, Any] = None) -> Tuple[List[SearchResult], str]:
        """단일 도구를 비동기적으로 실행하며, 실행 전 쿼리를 최적화합니다."""
        if tool not in self.tool_mapping:
            print(f"- 알 수 없는 도구: {tool}")
            return [], ""

        original_query = inputs.get("query", "")
        
        # Vector DB 검색에서 graph_probe 정보 활용
        if tool == "vector_db_search" and state:
            import os
            
            # 전자 방식 (Graph-to-Vector) 제어 환경변수 체크
            disable_graph_to_vector = os.environ.get("DISABLE_GRAPH_TO_VECTOR", "false").lower() == "true"
            
            graph_probe = state.get("metadata", {}).get("graph_probe", {})
            docrels = graph_probe.get("docrels", [])
            isfrom = graph_probe.get("isfrom", [])
            nutrients = graph_probe.get("nutrients", [])
            precheck_disabled = graph_probe.get("precheck_disabled", False)
            
            print(f"  - vector_db_search 설정: precheck_disabled={precheck_disabled}, "
            f"graph_to_vector_disabled={disable_graph_to_vector}, "
            f"docrels={len(docrels)}, isfrom={len(isfrom)}, nutrients={len(nutrients)}")

            evidence_exists = bool(docrels or isfrom or nutrients)

            if disable_graph_to_vector:
                print(f"  🔴 Graph-to-Vector DISABLED: DISABLE_GRAPH_TO_VECTOR={os.environ.get('DISABLE_GRAPH_TO_VECTOR')}")
                print(f"  - Graph-to-Vector 비활성화: 기본 쿼리 최적화 수행")
                optimized_query = await self._optimize_query_for_tool(original_query, tool)
            else:
                print(f"  🟢 Graph-to-Vector ENABLED: DISABLE_GRAPH_TO_VECTOR={os.environ.get('DISABLE_GRAPH_TO_VECTOR')}")
                print(f"  - Vector DB 검색: 실시간 Graph 조회 수행")
                # Vector DB 검색 시에는 실시간으로 Graph를 조회
                try:
                    from ...services.database.neo4j_rag_tool import neo4j_graph_search
                    report = await neo4j_graph_search(original_query)
                    text = str(report) if report else ""
                    
                    # 실시간으로 Graph 증거 파싱
                    realtime_docrels = self._parse_docrels_from_graph_report(text)
                    realtime_isfrom = self._parse_isfrom_from_graph_report(text)  
                    realtime_nutrients = self._parse_nutrients_from_graph_report(text)

                    print(f"docrels {realtime_docrels}")
                    print(f"isfrom {realtime_isfrom}")
                    print(f"nutrients {realtime_nutrients}")
                    
                    realtime_evidence_exists = bool(realtime_docrels or realtime_isfrom or realtime_nutrients)
                    print(f"  - 실시간 Graph 조회 결과: docrels={len(realtime_docrels)}, isfrom={len(realtime_isfrom)}, nutrients={len(realtime_nutrients)}")
                    
                    if realtime_evidence_exists:
                        # ❶ LLM으로 '선별'
                        selected = await self._select_relevant_graph_evidence(
                        original_query, realtime_docrels, realtime_isfrom, realtime_nutrients, k_per_type=50
                        )
                        print(f"  - 선별 결과: docrels={len(selected['docrels'])}, isfrom={len(selected['isfrom'])}, nutrients={len(selected['nutrients'])}")
                        print(f" selected docrels: {selected['docrels']}")
                        print(f" selected isfrom: {selected['isfrom']}")
                        print(f" selected nutrients: {selected['nutrients']}")

                        # ❷ 선별 결과로 '재작성'
                        optimized_query = await self._refine_query_from_graph_all(
                            original_query, selected["docrels"], selected["isfrom"], selected["nutrients"]
                        )
                        print(f"  - 최적화된 쿼리: '{optimized_query}'")
                        # state 업데이트 (메타데이터 태깅을 위해)
                        docrels = selected["docrels"]
                        isfrom = selected["isfrom"]
                        nutrients = selected["nutrients"]
                    else:
                        print("  - 실시간 Graph 조회: 증거 없음, 기본 쿼리 최적화 수행")
                        optimized_query = await self._optimize_query_for_tool(original_query, tool)
                except Exception as e:
                    print(f"  - 실시간 Graph 조회 실패: {e}, 기본 쿼리 최적화 수행")
                    optimized_query = await self._optimize_query_for_tool(original_query, tool)
            #else:
                #print(f"  - 그래프 증거 없음: 기본 쿼리 최적화 수행")
                #optimized_query = await self._optimize_query_for_tool(original_query, tool)
        else:
            # [기존] 실제 도구 실행 전, 쿼리 최적화 단계 추가
            optimized_query = await self._optimize_query_for_tool(original_query, tool)

        # 최적화된 쿼리로 새로운 inputs 딕셔너리 생성
        optimized_inputs = inputs.copy()
        optimized_inputs["query"] = optimized_query

        try:
            print(f"\n>> DataGatherer: '{tool}' 도구 실행 (쿼리: '{optimized_query}')")
            result = await self.tool_mapping[tool](**optimized_inputs)
            
            # Vector DB에서 Graph-to-Vector 사용 시 메타데이터 태깅
            if tool == "vector_db_search" and state:
                import os
                disable_graph_to_vector = os.environ.get("DISABLE_GRAPH_TO_VECTOR", "false").lower() == "true"
                graph_probe = state.get("graph_probe", {})
                docrels = graph_probe.get("docrels", [])
                isfrom = graph_probe.get("isfrom", [])
                nutrients = graph_probe.get("nutrients", [])
                precheck_disabled = graph_probe.get("precheck_disabled", False)
                
                print(f"  - [DEBUG] Vector DB 메타데이터 태깅 조건 확인:")
                print(f"    - disable_graph_to_vector: {disable_graph_to_vector}")
                print(f"    - precheck_disabled: {precheck_disabled}")
                print(f"    - docrels count: {len(docrels)}")
                print(f"    - isfrom count: {len(isfrom)}")
                print(f"    - nutrients count: {len(nutrients)}")
                print(f"    - result count: {len(result) if result else 0}")
                print(f"    - graph_probe keys: {list(graph_probe.keys())}")
                
                evidence_exists = bool(docrels or isfrom or nutrients)
                print(f"    - evidence_exists: {evidence_exists}")
                print(f"    - 최종 조건: not {disable_graph_to_vector} and {evidence_exists}")
                
                if not disable_graph_to_vector and evidence_exists:
                    print(f"  - [DEBUG] Graph-to-Vector 메타데이터 태깅 시작")
                    # Graph-to-Vector가 사용된 경우 메타데이터 태깅
                    tagged_count = 0
                    for i, search_result in enumerate(result):
                        print(f"    - 결과 {i+1}: type={type(search_result)}, hasattr metadata={hasattr(search_result, 'metadata')}")
                        if hasattr(search_result, 'metadata'):
                            md = getattr(search_result, "metadata", {}) or {}
                            print(f"      - 기존 metadata keys: {list(md.keys())}")
                            # 쿼리 최적화에 사용된 Graph 증거 JSON 생성 (preview_json과 동일한 형태)
                            graph_evidence_json = {
                                "docrels": docrels,
                                "isfrom": isfrom,
                                "nutrients": nutrients
                            }
                            
                            md.update({
                                "derived_from": "graph_evidence",
                                "refined_query": optimized_query,
                                "original_query": original_query,
                                "evidence_counts": {
                                    "docrels": len(docrels),
                                    "isfrom": len(isfrom),
                                    "nutrients": len(nutrients)
                                },
                                "evidence_details": {
                                    "docrels": docrels,  # 모든 문서관계 정보 저장
                                    "isfrom": isfrom,    # 모든 원산지 정보 저장
                                    "nutrients": nutrients  # 모든 영양소 정보 저장
                                },
                                "graph_evidence_json": graph_evidence_json  # 쿼리 최적화에 사용된 실제 Graph 증거
                            })
                            search_result.metadata = md
                            print(f"      - 새 metadata keys: {list(md.keys())}")
                            print(f"      - [DEBUG] docrels 타입: {type(docrels)}, 개수: {len(docrels) if isinstance(docrels, list) else 'N/A'}")
                            print(f"      - [DEBUG] docrels 샘플: {docrels[:2] if isinstance(docrels, list) and len(docrels) > 0 else docrels}")
                            print(f"      - [DEBUG] evidence_details 설정됨: {type(md.get('evidence_details'))}")
                            tagged_count += 1
                    print(f"  - Graph-to-Vector 메타데이터 태깅 완료: {tagged_count}개 결과")
                else:
                    print(f"  - [DEBUG] Graph-to-Vector 메타데이터 태깅 조건 불만족")
            
            return result, optimized_query
        except Exception as e:
            print(f"- {tool} 실행 오류: {e}")
            return [], optimized_query


    async def execute_parallel(self, tasks: List[Dict[str, Any]], state: Dict[str, Any] = None) -> Dict[str, List[SearchResult]]:
        """여러 데이터 수집 작업을 병렬로 실행합니다."""
        
        # Graph-to-Vector 설정 확인
        import os
        disable_graph_to_vector = os.environ.get("DISABLE_GRAPH_TO_VECTOR", "false").lower() == "true"
        
        # 작업 분류
        vector_tasks = [task for task in tasks if task.get("tool") == "vector_db_search"]
        graph_tasks = [task for task in tasks if task.get("tool") == "graph_db_search"]
        other_tasks = [task for task in tasks if task.get("tool") not in ["vector_db_search", "graph_db_search"]]
        
        # Graph-to-Vector가 활성화되고 Vector 작업이 있는 경우 순차 실행
        if not disable_graph_to_vector and vector_tasks:
            print(f"\n>> DataGatherer: Graph-to-Vector 활성화 - 순차 실행 모드")
            print(f"   - Graph 우선 실행: {len(graph_tasks)}개")
            print(f"   - Vector 후속 실행: {len(vector_tasks)}개") 
            print(f"   - 기타 병렬 실행: {len(other_tasks)}개")
            
            organized_results = {}
            
            # 1단계: Graph 검색 먼저 실행 (Graph 작업이 없으면 Vector 쿼리로 Graph 검색 생성)
            if not graph_tasks:
                # Vector 작업이 있지만 Graph 작업이 없는 경우, 첫 번째 Vector 쿼리로 Graph 검색 자동 생성
                first_vector_task = vector_tasks[0]
                vector_query = first_vector_task.get("inputs", {}).get("query", "")
                print(f"\n🔍 1단계: Graph 검색 자동 생성 (Vector 쿼리 기반: '{vector_query}')")
                
                # Graph 검색 실행
                try:
                    graph_results, _ = await self.execute("graph_db_search", {"query": vector_query}, state)
                    print(f"  - graph_db_search 완료: {len(graph_results)}개 결과")
                    organized_results["graph_db_search_0"] = graph_results
                except Exception as e:
                    print(f"  - graph_db_search 실행 오류: {e}")
                    organized_results["graph_db_search_0"] = []
            else:
                print("\n🔍 1단계: Graph 검색 실행")
                graph_coroutines = [self.execute(task.get("tool"), task.get("inputs", {}), state) for task in graph_tasks]
                graph_results = await asyncio.gather(*graph_coroutines, return_exceptions=True)
                
                for i, task in enumerate(graph_tasks):
                    tool_name = task.get("tool", f"unknown_tool_{i}")
                    result = graph_results[i]
                    
                    if isinstance(result, Exception):
                        print(f"  - {tool_name} 실행 오류: {result}")
                        organized_results[f"{tool_name}_{i}"] = []
                    else:
                        search_results, optimized_query = result
                        print(f"  - {tool_name} 완료: {len(search_results)}개 결과")
                        organized_results[f"{tool_name}_{len(organized_results)}"] = search_results
            
            # 2단계: Vector 검색 실행 (Graph 정보가 state에 반영됨)
            if vector_tasks:
                print("\n🔍 2단계: Vector 검색 실행 (Graph 정보 반영)")
                vector_coroutines = [self.execute(task.get("tool"), task.get("inputs", {}), state) for task in vector_tasks]
                vector_results = await asyncio.gather(*vector_coroutines, return_exceptions=True)
                
                for i, task in enumerate(vector_tasks):
                    tool_name = task.get("tool", f"unknown_tool_{i}")
                    result = vector_results[i]
                    
                    if isinstance(result, Exception):
                        print(f"  - {tool_name} 실행 오류: {result}")
                        organized_results[f"{tool_name}_{len(organized_results)}"] = []
                    else:
                        search_results, optimized_query = result
                        print(f"  - {tool_name} 완료: {len(search_results)}개 결과")
                        organized_results[f"{tool_name}_{len(organized_results)}"] = search_results
            
            # 3단계: 기타 검색들 병렬 실행
            if other_tasks:
                print(f"\n🔍 3단계: 기타 검색 {len(other_tasks)}개 병렬 실행")
                other_coroutines = [self.execute(task.get("tool"), task.get("inputs", {}), state) for task in other_tasks]
                other_results = await asyncio.gather(*other_coroutines, return_exceptions=True)
                
                for i, task in enumerate(other_tasks):
                    tool_name = task.get("tool", f"unknown_tool_{i}")
                    result = other_results[i]
                    
                    if isinstance(result, Exception):
                        print(f"  - {tool_name} 실행 오류: {result}")
                        organized_results[f"{tool_name}_{len(organized_results)}"] = []
                    else:
                        search_results, optimized_query = result
                        print(f"  - {tool_name} 완료: {len(search_results)}개 결과")
                        organized_results[f"{tool_name}_{len(organized_results)}"] = search_results
                        
            return organized_results
            
        else:
            # 기존 병렬 실행 모드
            print(f"\n>> DataGatherer: {len(tasks)}개 작업 병렬 실행 시작 (Graph-to-Vector 비활성화)")
            
            # 각 작업에 대해 execute 코루틴을 생성합니다. execute 내부에서 쿼리 최적화가 자동으로 일어납니다.
            coroutines = [self.execute(task.get("tool"), task.get("inputs", {}), state) for task in tasks]

            # asyncio.gather를 사용하여 모든 작업을 동시에 실행하고 결과를 받습니다.
            results = await asyncio.gather(*coroutines, return_exceptions=True)



        organized_results = {}
        for i, task in enumerate(tasks):
            tool_name = task.get("tool", f"unknown_tool_{i}")
            result = results[i]

            # 작업 실행 중 예외가 발생했는지 확인합니다.
            if isinstance(result, Exception):
                print(f"  - {tool_name} 병렬 실행 오류: {result}")
                organized_results[f"{tool_name}_{i}"] = []
            else:
                print(f"  - {tool_name} 병렬 실행 완료: {len(result)}개 결과")
                print(f"  - {tool_name} 병렬 실행 결과: {result[:3]}...")  # 처음 3개 결과만 출력
                search_results, optimized_query = result  # 튜플 언패킹
                organized_results[f"{tool_name}_{i}"] = search_results

        return organized_results

    async def execute_parallel_streaming(self, tasks: List[Dict[str, Any]], state: Dict[str, Any] = None):
        """여러 데이터 수집 작업을 병렬로 실행하되, 각 작업이 완료될 때마다 실시간으로 yield합니다."""
        print(f"\n>> DataGatherer: {len(tasks)}개 작업 스트리밍 병렬 실행 시작")

        # 디버깅
        import pprint
        print("\n-- Tasks to be executed --")
        pprint.pprint(tasks, width=100, depth=2)
        print("\n-- Tasks to be executed --")

        # 각 태스크에 인덱스를 할당하여 순서를 추적
        async def execute_with_callback(task_index: int, task: Dict[str, Any]):
            tool_name = task.get("tool", f"unknown_tool_{task_index}")
            inputs = task.get("inputs", {})
            query = inputs.get("query", "")

            try:
                print(f"  - {tool_name} 시작: {query}")
                result, optimized_query = await self.execute(tool_name, inputs, state)  # state 전달
                print(f"  - {tool_name} 완료: {len(result)}개 결과")

                # 프론트엔드가 기대하는 형식으로 변환
                formatted_results = []
                for i, search_result in enumerate(result):
                    result_dict = search_result.model_dump()
                    
                    print(f"  - [DEBUG] 결과 {i+1} 포맷 변환:")
                    print(f"    - 원본 SearchResult metadata: {getattr(search_result, 'metadata', {})}")
                    print(f"    - model_dump metadata: {result_dict.get('metadata', {})}")
                    
                    formatted_result = {
                        "title": result_dict.get("title", "제목 없음"),
                        "content": result_dict.get("content", "content 없음"),
                        "url": result_dict.get("url", "url 없음"),
                        "source": result_dict.get("source", tool_name),
                        "score": result_dict.get("score", 0.0),
                        "metadata": result_dict.get("metadata", {}),  # 메타데이터 추가 - Graph-to-Vector 정보 보존
                    }
                    formatted_results.append(formatted_result)

                    metadata = formatted_result.get("metadata", {})
                    if metadata.get("derived_from") == "graph_evidence":
                        print(f"    - [SUCCESS] Graph-to-Vector 메타데이터 보존됨: {metadata}")
                    else:
                        print(f"    - [WARNING] Graph-to-Vector 메타데이터 없음: {metadata}")
                    
                    print(f"  - {tool_name} 결과 포맷 완료 (metadata keys: {list(formatted_result.get('metadata', {}).keys())})")

                return {
                    "step": task_index + 1,
                    "tool_name": tool_name,
                    "query": optimized_query,
                    "results": formatted_results,
                    "original_results": result  # 원본 SearchResult 객체들도 보존
                }

            except Exception as e:
                print(f"  - {tool_name} 실행 오류: {e}")
                return {
                    "step": task_index + 1,
                    "tool_name": tool_name,
                    "query": optimized_query,
                    "results": [],
                    "error": str(e),
                    "original_results": []
                }

        # 모든 작업을 비동기로 시작하고, 완료되는 대로 yield
        tasks_coroutines = [execute_with_callback(i, task) for i, task in enumerate(tasks)]

        # asyncio.as_completed를 사용하여 완료되는 순서대로 결과 처리
        collected_data = []

        for coro in asyncio.as_completed(tasks_coroutines):
            result = await coro
            collected_data.extend(result.get("original_results", []))

            # 개별 검색 결과를 즉시 yield
            yield {
                "type": "search_results",
                "data": {
                    "step": result["step"],
                    "tool_name": result["tool_name"],
                    "query": result["query"],
                    "results": result["results"],
                    "message_id": state.get("message_id") if state else None
                }
            }

        # 모든 검색이 완료된 후 전체 수집된 데이터를 마지막에 yield
        yield {
            "type": "collection_complete",
            "data": {
                "total_results": len(collected_data),
                # SearchResult 객체 리스트를 dict 리스트로 변환합니다.
                # SearchResult가 Pydantic 모델이라고 가정합니다.
                "collected_data": [res.model_dump() for res in collected_data]
            }
        }


    async def _web_search(self, query: str) -> List[SearchResult]:
        """웹 검색 실행 - 안정성 강화"""
        try:
            # 최적화된 쿼리 사용 (이미 _optimize_query_for_tool에서 처리됨)
            print(f"  - 웹 검색 실행 쿼리: {query}")

            # 전역 ThreadPoolExecutor 사용하여 병렬 처리
            loop = asyncio.get_event_loop()
            result_text = await loop.run_in_executor(
                _global_executor,  # 전역 executor 사용
                debug_web_search,
                query
            )

            # 결과가 문자열인 경우 파싱
            search_results = []
            if result_text and isinstance(result_text, str):
                # 간단한 파싱으로 SearchResult 객체 생성
                lines = result_text.split('\n')
                current_result = {}

                for line in lines:
                    line = line.strip()
                    if line.startswith(('1.', '2.', '3.', '4.', '5.')):
                        # 이전 결과 저장
                        if current_result:
                            search_result = SearchResult(
                                source="web_search",
                                content=current_result.get("snippet", ""),
                                search_query=query,
                                title=current_result.get("title", "웹 검색 결과"),
                                url=current_result.get("link"),
                                score=0.9,  # 웹검색 결과는 높은 점수
                                timestamp=datetime.now().isoformat(),
                                document_type="web",
                                metadata={"optimized_query": query, **current_result},
                                source_url=current_result.get("link", "웹 검색 결과")
                            )
                            search_results.append(search_result)

                        # 새 결과 시작
                        current_result = {"title": line[3:].strip()}  # 번호 제거
                    elif line.startswith("출처 링크:"):
                        current_result["link"] = line[7:].strip()  # "출처 링크:" 제거
                    elif line.startswith("요약:"):
                        current_result["snippet"] = line[3:].strip()

                # 마지막 결과 저장
                if current_result:
                    search_result = SearchResult(
                        source="web_search",
                        content=current_result.get("snippet", ""),
                        search_query=query,
                        title=current_result.get("title", "웹 검색 결과"),
                        url=current_result.get("link"),
                        score=0.9,  # 웹검색 결과는 높은 점수
                        timestamp=datetime.now().isoformat(),
                        document_type="web",
                        metadata={
                            "optimized_query": query,
                            "link": current_result.get("link"),  # 출처 링크 포함
                            **current_result
                        },
                        source_url=current_result.get("link", "웹 검색 결과")
                    )
                    search_results.append(search_result)

            print(f"  - 웹 검색 완료: {len(search_results)}개 결과")
            return search_results[:5]  # 상위 5개 결과만

        except concurrent.futures.TimeoutError:
            print(f"웹 검색 타임아웃: {query}")
            return []
        except Exception as e:
            print(f"웹 검색 오류: {e}")
            return []

    async def _vector_db_search(self, query: str) -> List[SearchResult]:
        """Vector DB 검색 실행 - 오류 처리 강화"""
        try:
            print(f">> Vector DB 검색 시작: {query}")

            # 전역 ThreadPoolExecutor 사용하여 병렬 처리
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                _global_executor,  # 전역 executor 사용
                vector_db_search,
                query
            )

            search_results = []
            for result in results:
                if isinstance(result, dict):
                    # 새로운 doc_link와 page_number 필드 사용
                    doc_link = result.get("source_url", "")
                    page_number = result.get("page_number", [])
                    # 문서 제목 추출
                    doc_title = result.get("title", "")
                    meta_data = result.get("meta_data", {})
                    # 제목에 페이지 번호 추가
                    full_title = f"{doc_title}, ({', '.join([f'p.{num}' for num in page_number])})".strip()
                    score = result.get("score", 5.2)
                    chunk_id = result.get("chunk_id", "")

                    search_results.append(SearchResult(
                        source="vector_db",
                        content=result.get("content", ""),
                        search_query=query,
                        title=full_title,
                        document_type="database",
                        score=score,
                        metadata = meta_data,
                        url=doc_link,  # 새 필드 추가
                        chunk_id = chunk_id,
                    ))


            print(f"  - Vector DB 검색 완료: {len(search_results)}개 결과")
            return search_results[:5]

        # except concurrent.futures.TimeoutError:
        #     print(f"Vector DB 검색 타임아웃: {query}")
        #     return []
        except Exception as e:
            print(f"Vector DB 검색 오류: {e}")

    async def _graph_db_search(self, query: str) -> List[SearchResult]:
        """Graph DB 검색 실행 - 포맷 불일치 방지 및 타임아웃 처리"""
        print(f"  - Graph DB 검색 시작: {query}")
        import concurrent.futures

        try:
            # graph_db_search가 동기 함수라면
            loop = asyncio.get_running_loop()
            raw_results = await loop.run_in_executor(None, graph_db_search, query)
            # 만약 langchain Tool일 경우:
            # with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            #     raw_results = executor.submit(graph_db_search.invoke, {"query": query}).result(timeout=20)

            search_results: List[SearchResult] = []

            if isinstance(raw_results, list):
                # list of dict or list of str
                for res in raw_results:
                    if isinstance(res, dict):
                        # Graph DB 결과의 의미있는 제목 생성
                        title_candidates = [
                            res.get("title"),
                            res.get("entity"),
                            res.get("product"),
                            res.get("name"),
                            res.get("품목명"),
                            res.get("원산지"),
                            res.get("영양소명")
                        ]
                        title = next((t for t in title_candidates if t), "그래프 정보")

                        # 관계형 데이터의 의미있는 내용 생성
                        content_parts = []
                        relationship_info = []

                        # 관계 정보 추출
                        for key, value in res.items():
                            if "관계" in key or "연결" in key or key.endswith("_관계"):
                                relationship_info.append(f"🔗 {key}: {value}")
                            elif key not in ['title', 'content', 'entity', 'product'] and value:
                                content_parts.append(f"• {key}: {value}")

                        if relationship_info:
                            content_parts = relationship_info + content_parts

                        content = res.get("content") or "\n".join(content_parts[:8]) or json.dumps(res, ensure_ascii=False, indent=2)
                        score = res.get("confidence") or res.get("score") or 0.8
                    else:
                        title = f"그래프 관계 정보"
                        content = str(res)
                        score = 0.8

                    search_results.append(
                        SearchResult(
                            source="graph_db",
                            content=content,
                            search_query=query,
                            title=title,
                            score=score,
                            document_type="graph",
                            url=""  # 빈 문자열로 통일 (None 쓰면 후단에서 깨지는 경우 있음)
                        )
                    )
            elif isinstance(raw_results, dict):
                # 단일 그래프 결과의 의미있는 제목 생성
                title_candidates = [
                    raw_results.get("title"),
                    raw_results.get("entity"),
                    raw_results.get("product"),
                    raw_results.get("name"),
                    raw_results.get("품목명")
                ]
                title = next((t for t in title_candidates if t), "그래프 관계 정보")

                # 구조화된 내용 생성
                content_parts = []
                for key, value in raw_results.items():
                    if key not in ['title', 'content', 'entity', 'product'] and value:
                        if "관계" in key or "연결" in key:
                            content_parts.append(f"🔗 {key}: {value}")
                        else:
                            content_parts.append(f"• {key}: {value}")

                content = raw_results.get("content") or "\n".join(content_parts[:8]) or json.dumps(raw_results, ensure_ascii=False, indent=2)
                score = raw_results.get("confidence") or raw_results.get("score") or 0.8
                search_results.append(
                    SearchResult(
                        source="graph_db",
                        content=content,
                        search_query=query,
                        title=title,
                        score=score,
                        document_type="graph",
                        url=""
                    )
                )
            else:
                # 문자열 summary 같은 경우
                content = str(raw_results)
                title = "그래프 검색 요약"
                search_results.append(
                    SearchResult(
                        source="graph_db",
                        content=content,
                        search_query=query,
                        title=title,
                        score=0.6,
                        document_type="graph",
                        url=""
                    )
                )

            # # === 문서 관계(docrels) 기반 벡터 DB 후속 검색 ===
            # # Graph-to-Vector 설정 확인
            # import os
            # disable_graph_to_vector = os.environ.get("DISABLE_GRAPH_TO_VECTOR", "false").lower() == "true"
            
            # if not disable_graph_to_vector:
            #     print("  - Graph-to-Vector 활성화: Graph DB 후 Vector DB 확장 검색 시작")
            #     try:
            #         # 1) 그래프 리포트에서 증거 추출
            #         docrels = []
            #         isfrom  = []
            #         nutrs   = []

            #         if isinstance(raw_results, str):
            #             docrels = self._parse_docrels_from_graph_report(raw_results)
            #             isfrom  = self._parse_isfrom_from_graph_report(raw_results)
            #             nutrs   = self._parse_nutrients_from_graph_report(raw_results)
            #         elif isinstance(raw_results, dict):
            #             # 향후 구조화 반환을 대비한 폴백
            #             docrels = raw_results.get("docrels", []) if isinstance(raw_results.get("docrels"), list) else []
            #             isfrom  = raw_results.get("isfrom",  []) if isinstance(raw_results.get("isfrom"),  list) else []
            #             nutrs   = raw_results.get("nutrients",[]) if isinstance(raw_results.get("nutrients"),list) else []

            #         # 2) 그래프 전 evidence를 반영해 벡터 쿼리 재작성
            #         refined_query = await self._refine_query_from_graph_all(query, docrels, isfrom, nutrs)
            #         print(f"  - Graph evidence 기반 재작성 쿼리: {refined_query}")

            #         # 3) 벡터 DB 재검색 실행
            #         vec_results = await self._vector_db_search(refined_query)

            #         # 4) 메타데이터 태깅 및 점수 보정
            #         for vr in vec_results or []:
            #             md = getattr(vr, "metadata", {}) or {}
            #             md.update({
            #                 "derived_from": "graph_evidence",
            #                 "refined_query": refined_query,
            #                 "evidence_counts": {
            #                     "docrels": len(docrels),
            #                     "isfrom": len(isfrom),
            #                     "nutrients": len(nutrs)
            #                 },
            #                 "evidence_details": {
            #                     "docrels": docrels,
            #                     "isfrom": isfrom,
            #                     "nutrients": nutrs
            #                 },
            #                 "graph_evidence_json": {
            #                     "docrels": docrels,
            #                     "isfrom": isfrom,
            #                     "nutrients": nutrs
            #                 }
            #             })
            #             vr.metadata = md
            #             try:
            #                 vr.score = max(vr.score or 0.0, 0.85)
            #             except Exception:
            #                 pass

            #         search_results.extend(vec_results or [])
            #         print(f"  - Graph→Vector 확장 검색 완료: {len(vec_results or [])}개 추가 결과")

            #     except Exception as follow_err:
            #         print(f"  - Graph→Vector 후속 검색 중 오류: {follow_err}")
            # else:
            #     print("  - Graph-to-Vector 비활성화: Graph DB 후 Vector DB 확장 검색 생략")

            print(f"  - Graph DB 검색 완료: {len(search_results)}개 결과")
            return search_results

        except concurrent.futures.TimeoutError:
            print(f"Graph DB 검색 타임아웃: {query}")
            return []
        except Exception as e:
            print(f"Graph DB 검색 오류: {e}")
            return []

    async def _arxiv_search(self, query: str) -> List[SearchResult]:
        """arXiv 논문 검색 실행 - 학술 논문 기반 인사이트"""
        try:
            print(f"  - arXiv 논문 검색 시작: {query}")

            # arXiv 검색 함수를 직접 호출 (langchain tool 우회)
            loop = asyncio.get_event_loop()

            def _direct_arxiv_search(query_text, max_results=5):
                try:
                    # 검색 쿼리 URL 인코딩
                    base_url = "https://export.arxiv.org/api/query?"
                    ##search_query = urllib.parse.quote(query_text)

                    # arXiv API 파라미터
                    params = {
                        'search_query': f'all:{query}',
                        'start': 0,
                        'max_results': max_results,
                        'sortBy': 'lastUpdatedDate',
                        'sortOrder': 'descending'
                    }

                    # URL 생성
                    url = base_url + urllib.parse.urlencode(params)
                    print(f"  - API URL: {url}")

                    # API 호출
                    # response = urllib.request.urlopen(url, timeout=10)
                    # data = response.read().decode('utf-8')
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "MyArxivClient/1.0 (contact: youremail@example.com)"}
                    )

                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = resp.read().decode("utf-8")
                    # XML 파싱
                    root = ET.fromstring(data)

                    # 네임스페이스 정의
                    ns = {
                        'atom': 'http://www.w3.org/2005/Atom',
                        'arxiv': 'http://arxiv.org/schemas/atom'
                    }

                    # 결과 파싱
                    entries = root.findall('atom:entry', ns)

                    if not entries:
                        return f"'{query_text}'에 대한 arXiv 논문을 찾을 수 없습니다."

                    results = []
                    for i, entry in enumerate(entries[:max_results], 1):
                        # 논문 정보 추출
                        title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')

                        # 저자 정보
                        authors = entry.findall('atom:author', ns)
                        author_names = [author.find('atom:name', ns).text for author in authors]
                        author_str = ', '.join(author_names[:3])
                        if len(author_names) > 3:
                            author_str += f' 외 {len(author_names)-3}명'

                        # 초록
                        summary = entry.find('atom:summary', ns).text.strip()
                        if len(summary) > 300:
                            summary = summary[:297] + "..."

                        # 발행일
                        published = entry.find('atom:published', ns).text
                        pub_date = datetime.strptime(published[:10], '%Y-%m-%d').strftime('%Y년 %m월 %d일')

                        # 카테고리
                        categories = entry.findall('atom:category', ns)
                        cat_list = [cat.get('term') for cat in categories]
                        categories_str = ', '.join(cat_list[:3])

                        # 논문 링크
                        pdf_link = None
                        for link in entry.findall('atom:link', ns):
                            if link.get('type') == 'application/pdf':
                                pdf_link = link.get('href')
                                break

                        if not pdf_link:
                            pdf_link = entry.find('atom:id', ns).text.replace('abs', 'pdf')

                        # 결과 포맷팅
                        result_text = f"{i}. 📄 {title}\n"
                        result_text += f"   저자: {author_str}\n"
                        result_text += f"   발행일: {pub_date}\n"
                        result_text += f"   분야: {categories_str}\n"
                        result_text += f"   PDF: {pdf_link}\n"
                        result_text += f"   초록: {summary}\n"

                        results.append(result_text)

                    # 최종 결과 반환
                    final_result = f"arXiv 논문 검색 결과 (검색어: {query_text}):\n"
                    final_result += f"총 {len(results)}개 논문 발견\n\n"
                    final_result += "\n".join(results)

                    return final_result

                except Exception as e:
                    return f"arXiv 검색 중 오류 발생: {str(e)}"

            result_text = await loop.run_in_executor(
                _global_executor,
                _direct_arxiv_search,
                query,
                5
            )

            # 결과 파싱
            search_results = []
            if result_text and isinstance(result_text, str):
                lines = result_text.split('\n')
                current_paper = {}

                for line in lines:
                    line = line.strip()
                    if line.startswith(('1.', '2.', '3.', '4.', '5.')):
                        # 이전 논문 저장
                        if current_paper:
                            search_result = SearchResult(
                                source="arxiv_search",
                                content=current_paper.get("초록", ""),
                                search_query=query,
                                title=current_paper.get("title", "arXiv 논문"),
                                url=current_paper.get("pdf", ""),
                                score=0.95,  # 학술 논문은 높은 신뢰도
                                timestamp=datetime.now().isoformat(),
                                document_type="research_paper",
                                metadata={
                                    "authors": current_paper.get("저자", ""),
                                    "date": current_paper.get("발행일", ""),
                                    "categories": current_paper.get("분야", ""),
                                    "optimized_query": query
                                },
                                source_url=current_paper.get("pdf", "")
                            )
                            search_results.append(search_result)
                        # 새 논문 시작
                        current_paper = {}
                        title_part = line.split('📄', 1)
                        if len(title_part) > 1:
                            current_paper["title"] = title_part[1].strip()
                    elif '저자:' in line:
                        current_paper["저자"] = line.split('저자:', 1)[1].strip()
                    elif '발행일:' in line:
                        current_paper["발행일"] = line.split('발행일:', 1)[1].strip()
                    elif '분야:' in line:
                        current_paper["분야"] = line.split('분야:', 1)[1].strip()
                    elif 'PDF:' in line:
                        current_paper["pdf"] = line.split('PDF:', 1)[1].strip()
                    elif '초록:' in line:
                        current_paper["초록"] = line.split('초록:', 1)[1].strip()

                # 마지막 논문 저장
                if current_paper:
                    search_result = SearchResult(
                        source="arxiv_search",
                        content=current_paper.get("초록", ""),
                        search_query=query,
                        title=current_paper.get("title", "arXiv 논문"),
                        url=current_paper.get("pdf", ""),
                        score=0.95,
                        timestamp=datetime.now().isoformat(),
                        document_type="research_paper",
                        metadata={
                            "authors": current_paper.get("저자", ""),
                            "date": current_paper.get("발행일", ""),
                            "categories": current_paper.get("분야", ""),
                            "optimized_query": query
                        },
                        source_url=current_paper.get("pdf", "")
                    )
                    search_results.append(search_result)

            print(f"  - arXiv 검색 완료: {len(search_results)}개 논문")
            return search_results[:5]  # 최대 5개 논문만 반환

        except Exception as e:
            print(f"  - arXiv 검색 오류: {e}")
            return []


    async def _pubmed_search(self, query: str) -> List[SearchResult]:
        """PubMed 논문 검색 실행 - 학술 논문 기반 인사이트 (영어 번역 후 검색)"""
        try:
            print(f"  - PubMed 논문 검색 시작 (원문 쿼리): {query}")
            loop = asyncio.get_event_loop()

            # === [NEW] 1) 쿼리 영어 번역 (LLM) ==========================

            try:
                english_query_raw = (query or "").strip()

                # 코드펜스 제거 및 첫 비어있지 않은 라인만 사용
                if english_query_raw.startswith("```"):
                    # ```...``` 블록 안의 내용만 추출
                    end_idx = english_query_raw.rfind("```")
                    if end_idx != -1:
                        english_query_raw = english_query_raw[3:end_idx].strip()
                    # 흔한 'json', 'text' 같은 언어 태그 제거
                    english_query_raw = english_query_raw.replace("json", "").replace("text", "").strip()

                english_query = ""
                for line in english_query_raw.splitlines():
                    line = line.strip()
                    if line:
                        english_query = line
                        break

                # 안전 폴백
                if not english_query:
                    english_query = query
                    print("  - 번역 결과 비어있음: 원문 쿼리로 폴백")
                else:
                    print(f"  - 번역된 영어 검색어: {english_query}")

            except Exception as te:
                # 번역 실패 시 원문 사용
                english_query = query
                print(f"  - 번역 단계 오류, 원문으로 진행: {te}")
            # ============================================================

            def _direct_pubmed_search(query_text, max_results=5, original_query=None):
                try:
                    api_key = os.getenv("PUBMED_API_KEY", "").strip()

                    # 1) ESearch로 PMID 목록 가져오기
                    esearch_base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                    esearch_params = {
                        "db": "pubmed",
                        "term": query_text,        # 영어로 번역된 검색어 사용
                        "retstart": 0,
                        "retmax": max_results,
                        "retmode": "json",
                        "sort": "pub+date"        # 최신 발행일 우선
                    }
                    if api_key:
                        esearch_params["api_key"] = api_key

                    esearch_url = esearch_base + "?" + urllib.parse.urlencode(esearch_params)
                    req = urllib.request.Request(
                        esearch_url,
                        headers={"User-Agent": "MyPubMedClient/1.0 (contact: youremail@example.com)"}
                    )
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        search_json = json.loads(resp.read().decode("utf-8", errors="replace"))

                    idlist = search_json.get("esearchresult", {}).get("idlist", [])
                    if not idlist:
                        print("  - PubMed: 검색 결과 0건")
                        return []

                    # 2) EFetch로 상세(제목/초록/저자/DOI 등) 받기
                    efetch_base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                    efetch_params = {
                        "db": "pubmed",
                        "id": ",".join(idlist),
                        "retmode": "xml"
                    }
                    if api_key:
                        efetch_params["api_key"] = api_key

                    efetch_url = efetch_base + "?" + urllib.parse.urlencode(efetch_params)
                    req2 = urllib.request.Request(
                        efetch_url,
                        headers={"User-Agent": "MyPubMedClient/1.0 (contact: youremail@example.com)"}
                    )
                    with urllib.request.urlopen(req2, timeout=30) as resp2:
                        xml_text = resp2.read().decode("utf-8", errors="replace")

                    if not xml_text.lstrip().startswith("<?xml"):
                        raise RuntimeError("Non-XML response from PubMed efetch")

                    root = ET.fromstring(xml_text)

                    def _parse_date(article):
                        y = article.findtext(".//ArticleDate/Year") or article.findtext(".//JournalIssue/PubDate/Year")
                        m = article.findtext(".//ArticleDate/Month") or article.findtext(".//JournalIssue/PubDate/Month")
                        d = article.findtext(".//ArticleDate/Day") or article.findtext(".//JournalIssue/PubDate/Day")
                        month_map = {"Jan":"01","Feb":"02","Mar":"03","Apr":"04","May":"05","Jun":"06",
                                    "Jul":"07","Aug":"08","Sep":"09","Oct":"10","Nov":"11","Dec":"12"}
                        if m and len(m) == 3 and m in month_map:
                            m = month_map[m]
                        if y:
                            m = m or "01"
                            d = d or "01"
                            try:
                                return datetime.strptime(f"{y}-{m}-{d}", "%Y-%m-%d").strftime("%Y년 %m월 %d일")
                            except Exception:
                                return f"{y}년"
                        return ""

                    results: List[SearchResult] = []
                    for art in root.findall(".//PubmedArticle"):
                        pmid = art.findtext(".//PMID") or ""
                        title = (art.findtext(".//ArticleTitle") or "").strip()

                        # 초록
                        abs_elems = art.findall(".//Abstract/AbstractText")
                        if abs_elems:
                            parts = []
                            for t in abs_elems:
                                label = t.attrib.get("Label")
                                txt = (t.text or "").strip()
                                parts.append(f"{label}: {txt}" if label else txt)
                            summary = " ".join([p for p in parts if p])
                        else:
                            summary = ""

                        # 저자
                        author_elems = art.findall(".//AuthorList/Author")
                        authors = []
                        for a in author_elems:
                            last = a.findtext("LastName") or ""
                            fore = a.findtext("ForeName") or a.findtext("Initials") or ""
                            if last or fore:
                                authors.append((fore + " " + last).strip())
                        author_str = ", ".join(authors[:3])
                        if len(authors) > 3:
                            author_str += f" 외 {len(authors)-3}명"

                        # 저널/날짜/MeSH
                        journal = (art.findtext(".//Journal/Title") or "").strip()
                        pub_date = _parse_date(art)
                        mesh_terms = [(mh.text or "").strip() for mh in art.findall(".//MeshHeading/DescriptorName")]
                        mesh_str = ", ".join([t for t in mesh_terms if t][:3])

                        # DOI 또는 PubMed 링크
                        doi = None
                        for aid in art.findall(".//ArticleIdList/ArticleId"):
                            if (aid.attrib or {}).get("IdType", "").lower() == "doi":
                                doi = (aid.text or "").strip()
                                break
                        link = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

                        # SearchResult 구성
                        item = SearchResult(
                            source="pubmed_search",
                            content=(summary[:1000] + ("..." if len(summary) > 1000 else "")),
                            search_query=query_text,  # 영어 검색식
                            title=title or f"PMID {pmid}",
                            url=link,
                            score=0.95,
                            timestamp=datetime.now().isoformat(),
                            document_type="research_paper",
                            metadata={
                                "authors": author_str,
                                "date": pub_date,
                                "journal": journal,
                                "pmid": pmid,
                                "mesh_terms": mesh_terms[:10],
                                "categories": mesh_str,
                                "optimized_query": query_text,      # 번역/최적화된 쿼리
                                "original_query": original_query or query_text  # [NEW] 원문 쿼리 보존
                            },
                            source_url=link
                        )
                        results.append(item)

                    return results[:max_results]

                except Exception as e:
                    print(f"  - PubMed 검색 중 오류: {e}")
                    return []

            # === [CHANGED] 2) 영어 쿼리로 PubMed 호출 ===================
            results = await loop.run_in_executor(
                _global_executor, _direct_pubmed_search, english_query, 5, query  # original_query 전달
            )
            # ============================================================

            print(f"  - PubMed 검색 완료: {len(results)}개 논문 (검색식: {english_query})")
            return results

        except Exception as e:
            print(f"  - PubMed 검색 오류: {e}")
            return []


    async def _rdb_search(self, query: str) -> List[SearchResult]:
        """RDB 검색 실행 - 반환 표준화"""
        try:
            loop = asyncio.get_running_loop()
            result_text = await loop.run_in_executor(None, rdb_search, query)

            # rdb_search는 문자열을 반환하므로, 이를 단일 SearchResult로 변환
            if isinstance(result_text, str) and result_text.strip():
                # "PostgreSQL 검색 결과: "로 시작하는지 체크
                if "PostgreSQL 검색 결과:" in result_text and "찾을 수 없습니다" in result_text:
                    # 검색 결과가 없는 경우
                    print(f"  - RDB에서 '{query}' 관련 데이터 없음")
                    return []

                search_result = SearchResult(
                    source="rdb_search",
                    content=result_text,
                    search_query=query,
                    title="PostgreSQL 데이터베이스 검색 결과",
                    url="",
                    score=0.9,
                    document_type="database",
                    metadata={"raw_result": result_text}
                )
                print(f"  - rdb_search 래퍼 반환: 1개 (텍스트 결과)")
                return [search_result]
            else:
                print(f"  - RDB 검색 결과가 비어있음")
                return []


        except Exception as e:
            print(f"RDB 검색 오류: {e}")
            return []

    async def _scrape_content(self, url: str, query: str = "") -> List[SearchResult]:
        """웹페이지 스크래핑 실행"""
        try:
            print(f">> 스크래핑 시작: {url}")

            # scrape_and_extract_content는 JSON 문자열을 받아야 함
            action_input = json.dumps({"url": url, "query": query})

            content = await asyncio.get_event_loop().run_in_executor(
                _global_executor, scrape_and_extract_content, action_input
            )

            if content:
                # URL에서 제목 추출
                title = url.split("/")[-1] if "/" in url else url
                if title.endswith('.pdf'):
                    title = f"PDF: {title}"
                else:
                    title = f"웹페이지: {title}"

                search_result = SearchResult(
                    source="scraper",
                    content=content,
                    search_query=query,
                    title=title,
                    url=url,
                    score=0.9,  # 명시적 스크래핑이므로 높은 점수
                    timestamp=datetime.now().isoformat(),
                    document_type="web_scraping",
                    metadata={
                        "scraped_url": url,
                        "content_length": len(content),
                        "scraping_query": query
                    },
                    chunk_id=f"scrape_{hash(url)}"
                )
                print(f"  - 스크래핑 완료: {len(content)}자")
                return [search_result]

            return []
        except Exception as e:
            print(f"웹 스크래핑 오류: {e}")
            return []



class ProcessorAgent:
    """데이터 가공 및 생성 전담 Agent (ReAct 제거, 순차 생성 지원)"""

    def __init__(self, model_pro: str = "gemini-2.5-pro", model_flash: str = "gemini-2.5-flash-lite", temperature: float = 0.3):
        # 보고서 최종 생성을 위한 고품질 모델 (Gemini Pro) - 품질 향상을 위해 Pro 사용
        self.llm_pro = ChatGoogleGenerativeAI(model=model_pro, temperature=temperature)
        # 구조 설계, 요약 등 빠른 작업에 사용할 경량 모델 (Gemini)
        self.llm_flash = ChatGoogleGenerativeAI(model=model_flash, temperature=0.1)

        # Fallback 모델들 (3개 키 + OpenAI)
        import sys
        import os
        sys.path.append('/app/utils')
        try:
            from api_fallback import api_manager
        except ImportError:
            print("⚠️ api_fallback 모듈을 찾을 수 없음, 기본 방식 사용")
            api_manager = None

        if api_manager:
            try:
                self.llm_pro_backup = api_manager.create_langchain_model(model_pro, temperature=temperature)
                self.llm_flash_backup = api_manager.create_langchain_model(model_flash, temperature=0.1)
                print(f"ProcessorAgent: Fallback 모델 초기화 완료 (사용 API: {api_manager.last_successful_api})")
            except Exception as e:
                print(f"ProcessorAgent: Fallback 모델 초기화 실패: {e}")
                self.llm_pro_backup = None
                self.llm_flash_backup = None
        else:
            self.llm_pro_backup = None
            self.llm_flash_backup = None

        # OpenAI fallback 모델들
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if self.openai_api_key:
            self.llm_openai_mini = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.1,
                api_key=self.openai_api_key
            )
            self.llm_openai_4o = ChatOpenAI(
                model="gpt-4o",
                temperature=temperature,
                api_key=self.openai_api_key
            )
            print("ProcessorAgent: OpenAI fallback 모델 초기화 완료")
        else:
            self.llm_openai_mini = None
            self.llm_openai_4o = None
            print("ProcessorAgent: 경고: OPENAI_API_KEY가 설정되지 않음")

        self.personas = PERSONA_PROMPTS

        # Orchestrator가 호출할 수 있는 작업 목록 정의
        self.processor_mapping = {
            "design_report_structure": self._design_report_structure,
            "create_chart_data": self._create_charts,
        }

    async def _invoke_with_fallback(self, prompt, primary_model, backup_model, fallback_model):
        """
        Gemini key 1 → Gemini key 2 → OpenAI 순으로 fallback 처리
        """
        # Gemini key 1 시도
        try:
            result = await primary_model.ainvoke(prompt)
            return result
        except Exception as e:
            print(f"ProcessorAgent: Gemini key 1 실패: {e}")

        # Gemini key 2 시도
        if backup_model:
            try:
                result = await backup_model.ainvoke(prompt)
                print("ProcessorAgent: Gemini key 2 성공")
                return result
            except Exception as e:
                print(f"ProcessorAgent: Gemini key 2 실패: {e}")

        # OpenAI 시도
        if fallback_model:
            try:
                result = await fallback_model.ainvoke(prompt)
                print("ProcessorAgent: OpenAI fallback 성공")
                return result
            except Exception as fallback_error:
                print(f"ProcessorAgent: OpenAI fallback도 실패: {fallback_error}")
                raise fallback_error
        else:
            print("ProcessorAgent: 모든 fallback 모델이 없음")
            raise Exception("모든 API 키 시도 실패")

    async def _astream_with_fallback(self, prompt, primary_model, backup_model, fallback_model):
        """
        스트리밍을 위한 Gemini key 1 → Gemini key 2 → OpenAI 순 fallback 처리
        """
        # Gemini key 1 시도
        primary_chunks_received = 0
        primary_content_length = 0

        try:
            print(f"- Primary 모델로 스트리밍 시도 ({type(primary_model).__name__})")
            async for chunk in primary_model.astream(prompt):
                primary_chunks_received += 1
                if hasattr(chunk, 'content') and chunk.content:
                    primary_content_length += len(chunk.content)
                yield chunk

            print(f"- Primary 스트리밍 완료: {primary_chunks_received}개 청크, {primary_content_length} 문자")

            # 청크를 받았지만 내용이 비어있는 경우도 실패로 간주
            if primary_chunks_received == 0 or primary_content_length == 0:
                print(f"- Primary 모델에서 유효한 내용이 생성되지 않음, backup 시도")
                raise Exception("No valid content generated")
            return

        except Exception as e:
            print(f"ProcessorAgent: Gemini key 1 실패: {e}")

        # Gemini key 2 시도
        if backup_model:
            try:
                print("ProcessorAgent: Gemini key 2로 스트리밍 시작")
                backup_chunks_received = 0
                async for chunk in backup_model.astream(prompt):
                    backup_chunks_received += 1
                    yield chunk
                print(f"ProcessorAgent: Gemini key 2 완료: {backup_chunks_received}개 청크")
                return
            except Exception as e:
                print(f"ProcessorAgent: Gemini key 2도 실패: {e}")

        # OpenAI 시도
        if fallback_model:
            try:
                print("ProcessorAgent: OpenAI fallback으로 스트리밍 시작")
                fallback_chunks_received = 0
                async for chunk in fallback_model.astream(prompt):
                    fallback_chunks_received += 1
                    yield chunk
                print(f"ProcessorAgent: OpenAI fallback 완료: {fallback_chunks_received}개 청크")
            except Exception as fallback_error:
                print(f"ProcessorAgent: OpenAI fallback도 실패: {fallback_error}")
                raise fallback_error
        else:
            print("ProcessorAgent: 모든 fallback 모델이 없음")
            raise Exception("모든 API 키 시도 실패")

    async def process(self, processor_type: str, data: Any, param2: Any, param3: str, param4: str = "", yield_callback=None, state: Optional[Dict[str, Any]] = None):
        """Orchestrator로부터 동기식 작업을 받아 처리합니다."""
        session_id = state.get("session_id", "unknown") if state else "unknown"
        logger = get_session_logger(session_id, "ProcessorAgent")
        logger.info(f"Processor 실행: {processor_type}")
        # state는 이미 매개변수로 전달받음

        if processor_type == "design_report_structure":
            selected_indexes = param2
            original_query = param3
            async for result in self._design_report_structure(data, selected_indexes, original_query, state):
                yield result
        elif processor_type == "create_chart_data":
            section_title = param2
            generated_content = param3
            # yield_callback은 이미 매개변수로 전달받음
            async for result in self._create_charts(data, section_title, generated_content, yield_callback, state):
                yield result

        else:
            yield {"type": "error", "data": {"error": f"알 수 없는 처리 타입: {processor_type}"}}

    async def _design_report_structure(self, data: List[SearchResult], selected_indexes: List[int], query: str, state: Optional[Dict[str, Any]] = None):
        """보고서 구조 설계 + 섹션별 사용할 데이터 인덱스 선택"""

        print(f"\n>> 보고서 구조 설계 시작:")
        print(f"   전체 데이터: {len(data)}개")
        print(f"   선택된 인덱스: {selected_indexes} ({len(selected_indexes)}개)")

        # 현재 날짜 정보 추가
        import pytz
        kst = pytz.timezone('Asia/Seoul')
        current_date_str = datetime.now(kst).strftime("%Y년 %m월 %d일")

        # 페르소나 정보 추출
        persona_name = state.get("persona", "기본") if state else "기본"
        persona_description = self.personas.get(persona_name, {}).get("description", "일반적인 분석가")
        print(f"  - 보고서 구조 설계에 '{persona_name}' 페르소나 관점 적용")

        # 선택된 인덱스의 데이터를 인덱스와 함께 매핑하여 컨텍스트 생성
        indexed_context = ""
        for idx in selected_indexes:
            if 0 <= idx < len(data):
                res = data[idx]
                source = getattr(res, 'source', 'Unknown')
                title = getattr(res, 'title', 'No Title')
                content = getattr(res, 'content', '')  # 전체 내용 (요약 없이)

                indexed_context += f"""
    --- 데이터 인덱스 [{idx}] ---
    출처: {source}
    제목: {title}
    내용: {content}

    """

        # 컨텍스트 길이 제한 (너무 길면 잘라내기)
        limited_indexed_context = indexed_context[:20000]  # 더 많은 정보 포함

        print(f"   생성된 컨텍스트 길이: {len(indexed_context)} 문자")
        print(f"   제한된 컨텍스트 길이: {len(limited_indexed_context)} 문자")

        # - 사용자의 '원본 질문(query)'을 명확히 제시하고, 구조 설계의 모든 과정이 이 질문에 답하는 데 집중되도록 지시
        # - 각 데이터가 원본 질문과 얼마나 관련 있는지 평가하여 부적합한 데이터는 사용하지 않도록 명시
        # - 섹션 제목이 원본 질문의 핵심 요소와 직접적으로 연결되도록 규칙 강화
        prompt = f"""
    당신은 '{persona_name}'({persona_description})의 관점을 가진 데이터 분석가이자 AI 에이전트 워크플로우 설계자입니다.
    주어진 **사용자 원본 질문**과 **선별된 데이터**를 분석하여, **오직 질문에 대한 답변**으로만 구성된 **내용이 절대 중복되지 않는** 논리적인 보고서 목차를 설계하고, **각 섹션의 구체적인 목표**와 **각 섹션별로 사용할 데이터 인덱스**를 명확히 분배해주세요.

    **### 가장 중요한 원칙 ###**
    **1. 질문에만 집중:** 당신의 유일한 목표는 아래 **사용자 원본 질문**에 직접적으로 답하는 것입니다. 질문에서 벗어나는 주제가 데이터에 포함되어 있더라도, 질문과 관련 없으면 **과감히 무시하고 보고서 구조에 포함하지 마세요.**
    **2. 데이터 적합성 검증:** 각 데이터 조각이 **사용자 원본 질문**의 어떤 부분에 답할 수 있는지 먼저 평가하세요. 관련 없는 데이터는 보고서 생성에 사용해서는 안 됩니다.
    **3. 내용 반복 금지:** 각 섹션의 주제는 명확히 분리되어야 합니다.

    **사용자 원본 질문**: "{query}"
    **현재 날짜**: {current_date_str}

    **선별된 데이터 (인덱스와 전체 내용 포함)**:
    {limited_indexed_context}

    **작업 지침**:
    1. **보고서 목차 및 섹션별 목표 설계**
    - 주어진 데이터를 분석하고, **사용자 원본 질문을 분해**하여, 그 구성 요소에 직접 답하는 3~5개의 **고유하고 구체적인 섹션**으로 목차를 구성하세요.
    - **핵심 규칙**: 각 섹션의 제목은 **서로 다른 분석 관점이나 주제**를 다루어야 합니다. 모호하거나 유사한 제목은 절대 금지됩니다.

        - **절대 피해야 할 나쁜 예시 (주제가 유사하여 내용이 중복됨):**
            - "1. 만두 수출 현황 분석", "2. 주요 수출국 및 시장 동향"  (X)
            - "1. 시장 트렌드", "2. 최신 동향 분석" (X)

        - **반드시 따라야 할 좋은 예시 (주제가 명확히 분리됨):**
            - "1. **국가별 수출액 데이터**를 통한 핵심 시장 분석" (정량적, 순위)
            - "2. **소비자 선호도 및 최신 식품 트렌드** 분석" (정성적, 트렌드)
            - "3. **주요 경쟁사 전략 및 성공/실패 사례** 연구" (경쟁, 사례 분석)

    - **섹션 제목 규칙**: 섹션 제목은 **'사용자 질문의 핵심 키워드'**를 반드시 포함해야 합니다.

        - **좋은 예시 (사용자 질문: "원료별 대체식품 유형과 미생물 발효식품 R&D 현황"):**
          - "1. 원료 기반 대체식품의 유형별 분류"
          - "2. 미생물 발효 식품의 핵심 기술 및 원리"
          - "3. 미생물 발효 식품의 최신 연구개발(R&D) 동향"

        - **나쁜 예시 (사용자가 묻지 않은 내용 포함):**
          - "1. 글로벌 대체식품 시장 규모 분석" (X)
          - "2. 대체식품 소비자 인식 조사" (X)

    - **[매우 중요]** 각 섹션마다 `description` 필드를 작성할 때 반드시 아래 특별 규칙을 준수하세요. 'description' 필드는 나중에 다른 AI가 해당 섹션을 작성할 때 직접적인 가이드라인으로 사용됩니다.
        -**`description` 필드 작성 특별 규칙 (매우 중요!)**:
            - **`content_type: 'synthesis'`인 경우**:
                - 해당 섹션이 분석해야 할 핵심 질문이나 달성 목표를 **한 문장으로 명확하게** 기술합니다.
            - **`content_type: 'full_data_for_chart'`인 경우**:
                - 아래 4가지 항목을 반드시 포함하여 **구체적이고 논리적으로** 기술합니다. 이 내용은 차트 생성 AI에게 전달되는 명세서 역할을 합니다.
                    - **(1) 분석 목표:** 이 섹션과 차트를 통해 궁극적으로 보여주고자 하는 핵심 인사이트가 무엇인지 서술합니다.
                    - **(2) 차트 필요성:** 왜 텍스트가 아닌 차트로 표현하는 것이 더 효과적인지 이유를 서술합니다. (예: "연도별 시장 규모 변화 추이를 직관적으로 파악하기 위해", "주요 국가별 수출액을 명확하게 비교하기 위해")
                    - **(3) 시각화할 내용:** 차트를 구성하는 핵심 데이터 요소(라벨, 값, 항목 등)를 구체적으로 명시합니다.
            	        - **(Bar/Line Chart 예시:** "X축은 연도, Y축은 시장 규모(억원)")
            	        - **(Pie/Doughnut Chart 예시:** "각 조각(slice)은 품목명을, 각 조각의 값은 해당 품목의 점유율(%)을 의미")
            	        - **(Radar Chart 예시:** "각 꼭짓점(axis)은 '성장성', '안정성' 등 평가 항목을, 각 선(line)은 경쟁사를 의미")
            	    - **(4) 추천 차트 유형:** 데이터의 특성에 가장 적합한 차트 종류를 추천합니다. (예: "시계열 데이터이므로 Line chart가 적합함", "구성 비율을 보여주므로 Pie chart가 적합함")

    2. **각 섹션별 사용 데이터 선택**
    - **1단계에서 설계한 고유한 섹션 제목**을 기준으로, 각 섹션마다 `use_contents` 필드에 **조금 전 당신이 직접 정의한 `description`의 목표를 달성하는 데** 가장 적합한 데이터의 인덱스 번호들을 배열로 할당하세요.
    - **데이터 중복 할당 엄격 금지**: **서로 다른 섹션은 반드시 서로 다른 `use_contents` 목록을 가져야 합니다.** 동일한 데이터를 여러 섹션에서 사용하는 것을 최소화해야 합니다.
    - **최적 할당 원칙**: 각 데이터는 그것이 가장 핵심적으로 뒷받침할 수 있는 **단 하나의 섹션에만 할당**하는 것을 원칙으로 합니다.
    - **인덱스 범위 및 개수 제한**:
        - 위에 제시된 데이터 인덱스 중에서만 선택하세요: {selected_indexes}
        - 한 섹션에 너무 많은 데이터(5개 초과)를 할당하지 마세요.

    3. **'결론' 섹션 추가 (필수)**: 보고서의 가장 마지막에는 항상 '결론' 섹션을 포함하세요.
    - `content_type`은 'synthesis'로 설정
    - `use_contents`에는 주요 섹션들의 핵심 데이터를 종합하여 포함하세요. **(예외: '결론' 섹션은 다른 섹션에서 사용된 핵심 데이터를 중복 포함할 수 있습니다.)**

    4. **섹션별 최적 콘텐츠 유형 결정 (매우 중요)**:
    - 각 섹션의 `content_type`을 결정할 때, 차트가 **반드시 필요한지** 신중하게 판단하세요. 텍스트나 표로도 충분히 전달 가능한 정보는 `synthesis`로 지정해야 합니다.

    - **`full_data_for_chart`는 다음 조건 중 하나 이상을 명확히 만족할 때만 사용하세요:**
        - **(A) 명확한 비교:** 여러 항목(3개 이상)의 수치를 **비교**하여 순위나 차이를 보여줄 때 (예: 국가별 수출액, 제품별 판매량). Bar chart가 효과적입니다.
        - **(B) 시간적 변화:** 시간(연도별, 분기별, 월별 등)에 따른 데이터의 **변화 추세**를 보여줄 때 (예: 연도별 시장 규모 변화). Line chart가 효과적입니다.
        - **(C) 구성 비율:** 전체에 대한 각 부분의 **비율**이나 점유율을 보여줄 때 (예: 시장 점유율, 설문조사 응답 비율). Pie/Doughnut chart가 효과적입니다.

    - **다음의 경우에는 `synthesis`를 사용하세요:**
        - 단순한 사실, 특정 수치, 간단한 목록을 나열하는 경우 (예: 'A 제품의 가격은 5,000원').
        - 데이터가 정성적이거나 서술적인 분석, 개념 설명, 요약, 결론인 경우.
        - 수치 데이터가 일부 포함되어 있지만, 비교/추세/비율을 보여주는 명확한 스토리가 없는 경우.
        - **표(Table)가 차트보다 정보를 더 명확하고 정확하게 전달할 수 있다고 판단되는 경우.**

    **중요: 차트 생성 섹션 ('full_data_for_chart')에 대한 특별 규칙**:
    - 차트 생성이 필요한 섹션은 **기본 데이터 외에 추가적으로 통계/표/수치 데이터가 풍부한 데이터도 포함**해야 합니다
    - 다음과 같은 키워드가 포함된 데이터를 우선적으로 추가 선택하세요:
      * **수치 키워드**: "매출액", "점유율", "증가율", "감소율", "퍼센트", "%", "억원", "조원", "톤", "개"
      * **표/통계 키워드**: "표", "통계", "현황표", "데이터", "순위", "비교", "분석"
      * **시계열 키워드**: "2024년", "2025년", "월별", "분기별", "연도별", "변화"
    - 차트 생성 섹션의 `use_contents`는 **기본 데이터 3-5개 + 통계/수치 데이터 2-3개**로 구성하세요
    - 예시: [0, 2, 5] (기본 데이터) + [8, 12] (통계 데이터) = [0, 2, 5, 8, 12]

    5. **⭐ 매우 중요: 데이터 충분성 검증 (엄격한 기준)**:
    - **`full_data_for_chart` 섹션의 엄격한 검증 기준**:
      * **수치 데이터 필수**: 숫자, 퍼센트, 금액, 비율이 **명확히 제시된 데이터가 3개 이상** 있어야 함
      * **구조화된 데이터 필수**: 표, 통계표, 순위, 비교 데이터가 **구체적 수치와 함께** 제시되어야 함
      * **차트 생성 가능성 확인**:
        - 지역별/품목별/시기별 **구체적 수치 비교**가 가능한가?
        - "재배면적감소", "정식지연" 같은 **정성적 설명만으로는 불충분**
        - 반드시 "20% 감소", "3000톤 생산", "50만원/톤" 같은 **구체적 수치**가 있어야 함
      * **부족한 경우 반드시 `is_sufficient: false`** 설정하고 구체적인 추가 검색 쿼리 제안

    - **`synthesis` 섹션**: 설명, 분석에 필요한 기본 정보가 있으면 충분 (수치 데이터 불필요)

    - **`feedback_for_gatherer` 작성 시**:
      * 구체적인 수치 데이터를 요청하는 검색어 작성
      * 예: "2025년 7월 8월 강원도 호남지역 농산물 생산량 재배면적 통계 수치 데이터"
      * 예: "집중호우 피해 지역별 농산물 생산량 감소율 퍼센트 통계표"

    **섹션별 데이터 선택 예시**:
    - **차트 섹션**: "시장 규모 분석" (full_data_for_chart)
      → 기본: [0, 2, 5] (시장, 매출 관련) + 통계: [8, 12] (수치 테이블 포함) = [0, 2, 5, 8, 12]
    - **차트 섹션**: "지역별 생산 현황" (full_data_for_chart)
      → 기본: [1, 3, 7] (지역, 생산 관련) + 통계: [9, 15] (지역별 통계 포함) = [1, 3, 7, 9, 15]
    - **텍스트 섹션**: "소비자 트렌드" (synthesis) → [4, 6, 10] (소비자, 선호도 관련)
    - **결론 섹션**: "결론 및 제언" (synthesis) → [0, 1, 3, 5, 8] (각 섹션 핵심 데이터 종합)

    **출력 포맷 예시 (반드시 JSON 형식으로만 응답):**
   ```json
    {{
        "title": "국내 건강기능식품 시장 동향 및 소비자 트렌드 분석 보고서",
        "structure": [
            {{
                "section_title": "1. 국내 건강기능식품 시장 규모 및 성장 추이",
                "description": "(1) 분석 목표: 연도별 시장 규모 변화를 통해 성장 추세를 파악하고 미래 시장성을 예측합니다.\\n(2) 차트 필요성: 텍스트로 나열된 수치보다 시계열 그래프를 통해 시장의 성장 흐름을 한눈에 직관적으로 전달하기 위함입니다.\\n(3) 시각화할 내용: X축은 '연도', Y축은 '시장 규모(조 원)'를 나타내어 시간의 흐름에 따른 변화를 보여줍니다.\\n(4) 추천 차트 유형: 시간에 따른 연속적인 데이터 변화를 보여주는 데 가장 효과적인 'Line chart'를 추천합니다.",
                "content_type": "full_data_for_chart",
                "use_contents": [0, 3, 7, 10],
                "is_sufficient": true,
                "feedback_for_gatherer": ""
             }},
            {{
                "section_title": "2. 주요 품목별 시장 점유율",
                "description": "(1) 분석 목표: 주요 건강기능식품 품목의 시장 점유율을 비교하여 현재 시장을 주도하는 아이템을 파악합니다.\\n(2) 차트 필요성: 전체 시장에서 각 품목이 차지하는 비중을 시각적으로 명확하게 비교하기 위함입니다.\\n(3) 시각화할 내용: 각 조각(slice)은 '품목명'을 나타내고, 그 값은 해당 품목의 '시장 점유율(%)'을 의미합니다.\\n(4) 추천 차트 유형: 전체에 대한 각 부분의 비율을 보여주는 데 가장 적합한 'Pie chart'를 추천합니다.",
                "content_type": "full_data_for_chart",
                "use_contents": [1, 5, 9, 12],
                "is_sufficient": true,
                "feedback_for_gatherer": ""
            }},
            {{
                "section_title": "3. 최신 소비자 트렌드 및 인식 변화",
                "description": "최신 소비자 설문조사 및 검색 데이터를 바탕으로 건강기능식품 시장의 핵심 트렌드와 소비자 인식 변화를 분석합니다.",
                "content_type": "synthesis",
                "use_contents": [2, 4, 8],
                "is_sufficient": true,
                "feedback_for_gatherer": ""
             }},
            {{
                "section_title": "4. 주요 경쟁사별 시장 점유율 분석 (데이터 부족)",
                "description": "(1) 분석 목표: 주요 경쟁사들의 시장 점유율을 시각적으로 비교하여 시장 내 경쟁 구도를 명확히 파악합니다.\\n(2) 차트 필요성: 각 경쟁사의 위치를 직관적으로 비교하기 위해 Bar chart를 사용합니다.\\n(3) 시각화할 내용: X축은 '경쟁사명', Y축은 '시장 점유율(%)'을 의미합니다.\\n(4) 추천 차트 유형: 'Bar chart'를 추천합니다.",
                "content_type": "full_data_for_chart",
                "use_contents": [2, 8],
                "is_sufficient": false,
                "feedback_for_gatherer": {{
                    "tool": "vector_db_search",
                    "query": "2024년 2025년 국내 건강기능식품 시장 경쟁사별 점유율 통계 데이터 표"
                }}
            }},
            {{
                "section_title": "5. 결론 및 시장 전망",
                "description": "앞서 분석한 시장 규모, 품목별 점유율, 소비자 트렌드를 종합하여 최종 결론을 도출하고 향후 시장을 전망합니다.",
                "content_type": "synthesis",
                "use_contents": [0, 1, 2, 5, 7],
                "is_sufficient": true,
                "feedback_for_gatherer": ""
            }}
        ]
    }}
    ```

    **중요**: `use_contents` 배열에는 반드시 위에 제시된 인덱스 번호 {selected_indexes} 중에서만 선택하세요.
    """

        try:
            response = await self._invoke_with_fallback(
                prompt,
                self.llm_flash,
                self.llm_flash_backup,
                self.llm_openai_mini
            )

            print(f"  - 보고서 구조 설계 응답 길이: {len(response.content)} 문자")

            # JSON 파싱
            design_result = json.loads(re.search(r'\{.*\}', response.content, re.DOTALL).group())

            # 보고서 구조 설계 결과 JSON dump로 출력
            print(f"  - 보고서 구조 설계 결과 JSON:")
            print(json.dumps(design_result, ensure_ascii=False, indent=2))

            # ⭐ 핵심 추가: 인덱스 유효성 검증 및 데이터 충분성 재검증
            print(f"  - 구조 설계 결과 검증 및 데이터 충분성 재확인:")
            for i, section in enumerate(design_result.get("structure", [])):
                section_title = section.get("section_title", f"섹션 {i+1}")
                content_type = section.get("content_type", "synthesis")
                use_contents = section.get("use_contents", [])
                is_sufficient = section.get("is_sufficient", True)

                # 유효한 인덱스만 필터링
                valid_use_contents = []
                for idx in use_contents:
                    if isinstance(idx, int) and idx in selected_indexes:
                        valid_use_contents.append(idx)
                    else:
                        print(f"    경고: 잘못된 인덱스 {idx} 제거됨 (허용된 인덱스: {selected_indexes})")

                section["use_contents"] = valid_use_contents

                print(f"    '{section_title}' ({content_type}): {len(valid_use_contents)}개 데이터")
                print(f"      사용 인덱스: {valid_use_contents}")
                print(f"      LLM 판단 is_sufficient: {is_sufficient}")

                # ⭐ 차트 섹션에 대한 실제 데이터 내용 기반 재검증
                if content_type == "full_data_for_chart":
                    has_numeric_data = False
                    numeric_count = 0

                    print(f"      📊 차트 섹션 데이터 내용 검증:")
                    for idx in valid_use_contents:
                        if 0 <= idx < len(data):
                            data_item = data[idx]
                            content = getattr(data_item, 'content', '')
                            title = getattr(data_item, 'title', 'No Title')

                            # 수치 데이터 패턴 검사 (더 포괄적)
                            numeric_patterns = [
                                r'\d+%',                    # 퍼센트
                                r'\d+\.\d+%',               # 소수점 퍼센트
                                r'\d+억원?',                 # 억원
                                r'\d+조원?',                 # 조원
                                r'\d+만원?',                 # 만원
                                r'\d+천톤',                  # 천톤
                                r'\d+톤',                   # 톤
                                r'\d+,\d+',                 # 천의 자리 콤마
                                r'\d+\s*원/\s*\d+\s*개?',     # 단가 (원/개, 원/20개 등)
                                r'단위:\s*원/\d+개?',          # "단위: 원/20개" 형태
                                r'\d+\s+\d+\s+\d+\s+\d+',     # 연속된 숫자들 (테이블 데이터)
                                r'\d{4}년\s+\d+',           # "2025년 38660" 같은 연도+숫자
                                r'\d+월\s+\d+',             # "7월 14324" 같은 월+숫자
                                r'증가율.*?\d+',             # 증가율 + 숫자
                                r'감소율.*?\d+',             # 감소율 + 숫자
                                r'점유율.*?\d+',             # 점유율 + 숫자
                                r'\d+\s+\d+\s+\d+',         # 표 형태의 연속 숫자
                                r'평년\s+\d+',              # "평년 25686" 같은 기준값
                            ]

                            numeric_matches = 0
                            for pattern in numeric_patterns:
                                matches = re.findall(pattern, content)
                                numeric_matches += len(matches)

                            if numeric_matches > 0:
                                has_numeric_data = True
                                numeric_count += numeric_matches
                                print(f"        [{idx}] ✅ 수치 데이터 {numeric_matches}개 발견: {title[:30]}...")
                            else:
                                print(f"        [{idx}] ❌ 수치 데이터 없음: {title[:30]}...")

                    # 실제 데이터 기반 충분성 재판단 (더 관대한 기준)
                    # 테이블이나 시계열 데이터가 있으면 충분하다고 판단
                    has_table_data = any('단위:' in getattr(data[idx], 'content', '') or
                                       '구분' in getattr(data[idx], 'content', '') or
                                       ('년' in getattr(data[idx], 'content', '') and '월' in getattr(data[idx], 'content', ''))
                                       for idx in valid_use_contents if 0 <= idx < len(data))

                    # 가격/단가 정보도 차트 생성 가능하다고 판단
                    has_price_data = any(('원/' in getattr(data[idx], 'content', '') or
                                        '가격' in getattr(data[idx], 'title', ''))
                                       for idx in valid_use_contents if 0 <= idx < len(data))

                    actually_sufficient = (has_numeric_data and numeric_count >= 2) or has_table_data or has_price_data

                    print(f"        💡 테이블 형태 데이터 발견: {has_table_data}")
                    print(f"        💰 가격/단가 데이터 발견: {has_price_data}")
                    print(f"        📊 최종 충분성 판단: 수치({numeric_count}개) + 테이블({has_table_data}) + 가격({has_price_data}) = {actually_sufficient}")

                    if is_sufficient and not actually_sufficient:
                        print(f"      🔧 LLM 판단 오류 감지: sufficient=true였지만 실제 수치 데이터 부족 ({numeric_count}개)")
                        print(f"      🔄 is_sufficient를 false로 수정하고 추가 검색 요청 생성")
                        section["is_sufficient"] = False
                        section["feedback_for_gatherer"] = {
                            "tool": "vector_db_search",
                            "query": f"{section_title} 관련 구체적 수치 통계 데이터 표 그래프 퍼센트 금액 생산량"
                        }
                    elif actually_sufficient:
                        print(f"      ✅ 차트 생성 가능: 수치 데이터 {numeric_count}개 충분")
                    else:
                        print(f"      ⚠️  차트 생성 어려움: 수치 데이터 {numeric_count}개 부족")

                # 사용될 데이터 미리보기
                for idx in valid_use_contents[:2]:  # 처음 2개만
                    if 0 <= idx < len(data):
                        data_item = data[idx]
                        print(f"      [{idx:2d}] {getattr(data_item, 'source', 'Unknown'):10s} | {getattr(data_item, 'title', 'No Title')[:40]}")

            print(f"  - 보고서 구조 설계 완료: '{design_result.get('title', '제목없음')}'")
            yield {"type": "result", "data": design_result}

        except Exception as e:
            print(f"  - 보고서 구조 설계 실패: {e}")
            print(f"  - 안전 모드로 기본 구조 생성")

            # 안전 모드: 모든 선택된 인덱스를 하나의 섹션에서 사용
            fallback_design = {
                "title": f"{query} - 통합 분석 보고서",
                "structure": [{
                    "section_title": "종합 분석", "description": "수집된 데이터를 종합 분석합니다.",
                    "content_type": "synthesis", "use_contents": selected_indexes[:10],
                    "is_sufficient": True, "feedback_for_gatherer": ""
                }]
            }
            yield {"type": "result", "data": fallback_design}


    # worker_agents.py - ProcessorAgent 클래스의 수정된 함수들

    async def _synthesize_data_for_section(self, section_title: str, section_data: List[SearchResult]) -> str:
        """⭐ 수정: 섹션별 선택된 데이터만 사용하여 출처 번호 정확히 매핑"""

        # ⭐ 핵심 개선: 섹션별 선택된 데이터만 사용하여 출처 정보 생성
        context_with_sources = ""
        for i, res in enumerate(section_data):  # section_data만 사용 (all_data 대신)
            source_info = ""
            source_link = ""

            # Web search 결과인 경우
            if hasattr(res, 'source') and 'web_search' in str(res.source).lower():
                if hasattr(res, 'url') and res.url:
                    source_link = res.url
                    source_info = f"웹 출처: {res.url}"
                elif hasattr(res, 'metadata') and res.metadata and 'link' in res.metadata:
                    source_link = res.metadata['link']
                    source_info = f"웹 출처: {res.metadata['link']}"
                else:
                    source_info = "웹 검색 결과"
                    source_link = "웹 검색"

            # Vector DB 결과인 경우
            elif hasattr(res, 'source_url'):
                source_info = f"문서 출처: {res.source_url}"
                source_link = res.source_url
            elif hasattr(res, 'title'):
                source_info = f"문서: {res.title}"
                source_link = res.title
            else:
                source_name = res.source if hasattr(res, 'source') else 'Vector DB'
                source_info = f"출처: {source_name}"
                source_link = source_name

            # 핵심: 섹션 데이터 내에서의 인덱스 사용 (0, 1, 2...)
            context_with_sources += f"--- 문서 ID {i}: [{source_info}] ---\n제목: {res.title}\n내용: {res.content}\n출처_링크: {source_link}\n\n"

        prompt = f"""
    당신은 여러 데이터 소스를 종합하여 특정 주제에 대한 분석 보고서의 한 섹션을 저술하는 주제 전문가입니다.

    **작성할 섹션의 주제**: "{section_title}"

    **사용 데이터 인덱스**

    **참고할 선택된 데이터** (섹션별로 엄선된 관련 데이터):
    {context_with_sources[:8000]}

    **작성 지침**:
    1. **핵심 정보 추출**: '{section_title}' 주제와 직접적으로 관련된 핵심 사실, 수치, 통계 위주로 정보를 추출하세요.
    2. **간결한 요약**: 정보를 단순히 나열하지 말고, 1~2 문단 이내의 간결하고 논리적인 핵심 요약문으로 재구성해주세요.
    3. **중복 제거**: 여러 문서에 걸쳐 반복되는 내용은 하나로 통합하여 제거하세요.
    4. **객관성 유지**: 데이터에 기반하여 객관적인 사실만을 전달해주세요.
    5. **⭐ 출처 정보 보존**: 중요한 정보나 수치를 언급할 때 해당 정보의 출처를 [SOURCE:숫자] 형식으로 표기하세요. 반드시 숫자만 사용하세요.
    - **문서 ID 번호를 사용**
    - 예시: "시장 규모가 증가했습니다 [SOURCE:1]", "매출이 상승했습니다 [SOURCE:2]"
    - 잘못된 예시: [SOURCE:데이터 1], [SOURCE:문서 1] (이런 형식 사용 금지)
    6. **⭐ 노션 스타일 마크다운 적극 활용**:
    - **중요한 키워드나 수치**: **굵은 글씨**로 강조
    - *일반적인 강조나 트렌드*: *기울임체*로 표현
    - **핵심 포인트나 결론**: > 인용문 형태로 강조
    - **항목이 여러 개**: - 첫 번째 항목, - 두 번째 항목 형태
    - **하위 분류**:   - 세부 항목 (들여쓰기)
    - **단락 구분**: 내용 변화 시 공백 라인으로 명확히 구분

    **결과물 (핵심 요약본)**:
    """
        response = await self._invoke_with_fallback(
            prompt,
            self.llm_flash,
            self.llm_flash_backup,
            self.llm_openai_mini
        )
        return response.content

    async def generate_section_streaming(
        self,
        section: Dict[str, Any],
        full_data_dict: Dict[int, Dict],
        original_query: str,
        use_indexes: List[int],
        awareness_context: str = "",
        state: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[str, None]:
        """페르소나, 전체 구조 인지(awareness_context), 섹션 목표(description)를 반영하여 전체 데이터 딕셔너리에서 해당 섹션 인덱스만 사용하여 스트리밍 생성"""
        section_title = section.get("section_title", "제목 없음")
        content_type = section.get("content_type", "synthesis")
        description = section.get("description", "이 섹션의 내용을 요약합니다.")

        # 페르소나 정보 추출
        persona_name = state.get("persona", "기본") if state else "기본"
        default_report_prompt = "당신은 전문적인 AI 분석가입니다."
        persona_instruction = self.personas.get(persona_name, {}).get("report_prompt", default_report_prompt)

        print(f"  - 섹션 '{section_title}' 생성에 '{persona_name}' 페르소나 스타일 적용 (전체 구조 인지)")

        # 보고서 제목과 중복되지 않도록 섹션 제목 확인
        report_title = state.get("report_title", "") if state else ""

        # 첫 번째 섹션이고 제목이 보고서 제목과 유사한 경우 헤더 생략
        is_duplicate_title = (
            report_title and
            (section_title.replace(" ", "").lower() in report_title.replace(" ", "").lower() or
             report_title.replace(" ", "").lower() in section_title.replace(" ", "").lower())
        )

        # 중복되지 않는 경우에만 섹션 헤더 출력
        if not is_duplicate_title:
            section_header = f"\n\n## {section_title}\n\n"
            yield section_header
            print(f"  - 섹션 헤더 출력: {section_title}")
        else:
            print(f"  - 섹션 헤더 생략 (보고서 제목과 중복): {section_title} ≈ {report_title}")
            # 중복인 경우 간격만 추가
            yield "\n\n"

        prompt = "" # 최종 프롬프트를 담을 변수

        if content_type == "synthesis":
            print(f"\n🔍 === SECTION STREAMING 디버깅 ({section_title}) ===")
            print(f"use_indexes: {use_indexes}")
            print(f"full_data_dict 키들: {list(full_data_dict.keys()) if full_data_dict else 'None'}")

            # 전체 데이터 딕셔너리에서 해당 인덱스만 선별하여 프롬프트용으로 포맷팅
            section_data_content = ""
            valid_indexes = []
            for actual_index in use_indexes:
                if actual_index in full_data_dict:
                    valid_indexes.append(actual_index)
                    data_info = full_data_dict[actual_index]
                    section_data_content += f"**데이터 {actual_index}: {data_info['source']}**\n"
                    section_data_content += f"- **제목**: {data_info['title']}\n"
                    section_data_content += f"- **내용**: {data_info['content']}\n\n"
                    print(f"  ✅ [{actual_index}] 데이터 매핑 성공: '{data_info['title'][:30]}...'")
                else:
                    print(f"  ❌ [{actual_index}] full_data_dict에서 찾을 수 없음!")

            print(f"유효한 인덱스들: {valid_indexes}")
            print(f"프롬프트에서 사용할 SOURCE 번호들: {valid_indexes}")


            prompt_template = """

    {persona_instruction}

    당신은 위의 페르소나 지침을 따르는 전문가 AI입니다. 전체 보고서의 일부인 한 섹션을 작성하는 임무를 받았습니다.

    **사용자의 전체 질문**: "{original_query}"

    ---
    **[매우 중요] 전체 보고서 구조 및 당신의 역할**:
    당신은 아래 구조로 구성된 전체 보고서에서 **오직 '{section_title}' 섹션만**을 책임지고 있습니다.
    다른 전문가들이 나머지 섹션들을 동시에 작성하고 있으므로, **다른 섹션의 주제를 절대 침범하지 말고 당신의 역할에만 집중하세요.**

    {awareness_context}
    ---

    **현재 작성할 섹션 제목**: "{section_title}"
    **이 섹션의 핵심 목표**: "{description}"

    **참고 데이터 (실제 인덱스 번호 포함)**:
    {section_data_content}

    **작성 지침 (매우 중요)**:
    1. **역할 준수**: 위 '전체 보고서 구조'와 '핵심 목표'를 반드시 인지하고, '{section_title}'에 해당하는 내용만 깊이 있게 작성하세요.
    2. **페르소나 역할 유지**: 당신의 역할과 말투, 분석 관점을 반드시 유지하며 작성하세요.
    3. **간결성 유지**: 반드시 1~2 문단 이내로, 가장 핵심적인 내용만 간결하게 요약하여 작성하세요.
    4. **제목 반복 금지**: 주어진 섹션 제목을 절대 반복해서 출력하지 마세요. 바로 본문 내용으로 시작해야 합니다.
    5. **데이터 기반**: 참고 데이터에 있는 구체적인 수치, 사실, 인용구를 적극적으로 활용하여 내용을 구성하세요.
    6. **전문가적 문체**: 명확하고 간결하며 논리적인 전문가의 톤으로 글을 작성하세요.
    7. **⭐ 노션 스타일 마크다운 적극 활용 (매우 중요) - 반드시 지켜야 함**:

    **기본 포맷팅 (필수)**:
    - **핵심 키워드나 중요한 수치**: 반드시 **굵은 글씨**로 강조 (예: **58,000원/10kg**, **전년 대비 81.4% 하락**)
    - *변화나 추세*: 반드시 *기울임체*로 표현 (예: *전년 대비 감소*, *집중호우로 인한 피해*)
    - 문단별로 **반드시 2-3개 이상의 강조** 포함할 것

    **구조화 (필수)**:
    - **중요한 인사이트나 결론**: 반드시 `> **핵심 요약**: 내용` 형태로 블록쿼트 사용
    - **비교 정보가 3개 이상**: 반드시 마크다운 테이블 사용
    - **목록 형태 정보**: 반드시 `-` 불릿 포인트 사용
    - **세부 카테고리가 있으면**: 반드시 `### 소제목` 사용

    **필수 예시 패턴**:
    ```
    **배(Pear)**의 전체 재배면적은 **9,361ha**로 전년 대비 **0.6% 감소**했으며, *집중호우 피해 지역*으로 분류되는 강원 및 호남 지역에서의 변동이 두드러졌습니다. [SOURCE:2, 3]

    | 식재료 | 주요 생산지 | 재배면적 변화 | 주요 원인 |
    | :--- | :--- | :--- | :--- |
    | **배** | 전국 | **-0.6%** (전년 대비) | *전국적 재배면적 감소 추세* [SOURCE:2] |
    | **포도** | 전국 | **-2.3% ~ -6.7%** | *작형별 면적 감소* [SOURCE:4] |

    > **핵심 결론**: 집중호우 직접 피해는 *미미한 수준*이었으나, 고온 및 가뭄이 **과비대 지연** 등 품질에 미치는 영향이 더 컸습니다.
    ```

    **⚠️ 강제 요구사항**: 모든 문단에서 **굵은 글씨** 2개 이상, *기울임체* 1개 이상 반드시 사용

    **표 작성 지침**:
    - 3개 이상의 항목을 비교하거나 분류할 때 테이블 사용
    - 테이블 형식: `| 항목 | 내용 | 비고 |` 및 `| :--- | :--- | :--- |` 구분선
    - 테이블 셀 안에서도 **굵은 글씨**, *기울임체*, [SOURCE:숫자] 출처 표기 가능
    - 예시:
    ```
    | 주요 식재료 | 주요 생산지 | 현황 요약 |
    | :--- | :--- | :--- |
    | **토마토** | 호남 지역 | 집중호우로 **수해 발생** [SOURCE:2] |
    | **배** | 충남, 전남 | 피해는 **미미한 수준** [SOURCE:1] |
    ```
    8. **⭐ 출처 표기 (데이터 인덱스 번호 사용)**: 특정 정보를 참고하여 작성한 문장 바로 뒤에 [SOURCE:숫자] 형식으로 출처를 표기하세요.

    **🔴 매우 중요 - SOURCE 번호 사용 규칙**:
    - **반드시 위 "참고 데이터"에 명시된 "[데이터 인덱스 X]" 의 X 번호만 사용하세요**
    - 예: "데이터 0", "데이터 3", "데이터 7"이 주어졌다면 → [SOURCE:0], [SOURCE:3], [SOURCE:7]만 사용 가능
    - **존재하지 않는 번호는 절대 사용하지 마세요** (예: 데이터 0~7만 있는데 [SOURCE:15] 사용 금지)
    - 반드시 숫자만 사용하고 "데이터", "문서" 등의 단어는 사용하지 마세요
    - 여러 출처는 쉼표와 공백으로 구분: [SOURCE:1, 4, 8]
    - 단일 출처: [SOURCE:8]
    - SOURCE 태그는 완전한 문장이 끝난 후 바로 붙여서 작성하세요
    - SOURCE 태그 앞뒤로 줄바꿈하지 마세요 (스트리밍 청크 분할 방지)

    **올바른 예시** (데이터 0, 3, 8이 주어진 경우):
    - "**매출이 증가했습니다** [SOURCE:8]"
    - "시장 점유율이 상승했습니다 [SOURCE:3]"
    - "**매출이 5% 감소했습니다** [SOURCE:0, 3, 8]"

    **🚫 절대 금지되는 잘못된 예시**:
    - [SOURCE:데이터 1], [SOURCE:문서 1] (단어 포함 금지)
    - [SOURCE:1,\n4, 8] (줄바꿈 금지)
    - [SOURCE: 1 , 4 , 8] (불필요한 공백 금지)
    - [SOURCE:15] (위 참고 데이터에 없는 번호 사용 금지)

    **⚠️ 최종 체크리스트 (반드시 확인)**:
    □ **굵은 글씨**가 문단당 2개 이상 사용되었는가?
    □ *기울임체*가 적절히 사용되었는가?
    □ 3개 이상 비교 시 테이블을 사용했는가?
    □ 중요한 결론에 `> **핵심 요약**:` 블록쿼트를 사용했는가?
    □ [SOURCE:숫자] 형식으로 출처를 정확히 표기했는가?
    □ 단락 간 공백 라인이 있는가?

    **🚫 절대 금지 사항**:
    ❌ "추가 정보 요청", "더 많은 데이터가 필요합니다", "구체적인 데이터 부족" 등의 표현 사용 금지
    ❌ "...에 대한 추가 분석이 필요합니다" 같은 미완성 결론 제시 금지
    ✅ **현재 확보된 데이터로 최대한 구체적이고 완전한 분석 및 결론 제시 필수**
    ✅ 부족한 정보가 있어도 현재 데이터 기반으로 최선의 인사이트와 표 제공

    **⭐ 지금 바로 위 체크리스트를 모두 만족하는 마크다운 형식으로 섹션을 작성하세요:**

    **보고서 섹션 내용**:
    """

            prompt = prompt_template.format(
                persona_instruction=persona_instruction,
                original_query=original_query,
                section_title=section_title,
                awareness_context=awareness_context,
                description=description,
                section_data_content=section_data_content
            )

        else:  # "full_data_for_chart"
            section_data_content = ""
            for actual_index in use_indexes:
                if actual_index in full_data_dict:
                    data_info = full_data_dict[actual_index]
                    section_data_content += f"**데이터 {actual_index}: {data_info['source']}**\n"
                    section_data_content += f"- **제목**: {data_info['title']}\n"
                    section_data_content += f"- **내용**: {data_info['content']}\n"
                    section_data_content += f"- **출처_링크**: {data_info.get('url') or data_info.get('source_url', '')}\n\n"

            prompt_template = """
            {persona_instruction}

    당신은 데이터 분석가이자 보고서 작성가입니다. 위의 페르소나 지침을 따라서, 주어진 데이터를 분석하여 텍스트 설명과 시각적 차트를 결합한 전문가 수준의 보고서 섹션을 작성합니다.

    **사용자의 전체 질문**: "{original_query}"

    ---
    **[매우 중요] 전체 보고서 구조 및 당신의 역할**:
    당신은 아래 구조로 구성된 전체 보고서에서 **오직 '{section_title}' 섹션만**을 책임지고 있습니다.
    다른 전문가들이 나머지 섹션들을 동시에 작성하고 있으므로, **다른 섹션의 주제를 절대 침범하지 말고 당신의 역할에만 집중하세요.**

    {awareness_context}
    ---

    **현재 작성할 섹션 제목**: "{section_title}"
    **섹션 목표**: "{description}"

    **참고 데이터 (실제 인덱스 번호 포함)**:
    {section_data_content}

    **작성 지침 (매우 중요)**:
    1. **역할 준수**: 위 '전체 보고서 구조'와 '핵심 목표'를 반드시 인지하고, '{section_title}'에 해당하는 내용만 깊이 있게 작성하세요.
    2. **페르소나 역할 유지**: 당신의 역할과 말투, 분석 관점을 반드시 유지하며 작성하세요.
    3. **간결성 유지**: 반드시 1~2 문단 이내로, 데이터에서 가장 중요한 인사이트와 분석 내용만 간결하게 요약하여 작성하세요.
    4. **제목 반복 금지**: 주어진 섹션 제목을 절대 반복해서 출력하지 마세요. 바로 본문 내용으로 시작해야 합니다.
    5. **데이터 기반**: 설명에 구체적인 수치, 사실, 통계 자료를 적극적으로 인용하여 신뢰도를 높이세요.
    6. **차트 마커 삽입**: 텍스트 설명의 흐름 상, 시각적 데이터가 필요한 적절한 위치에 [GENERATE_CHART] 마커를 한 줄에 단독으로 삽입하세요.
    7. **서술 계속**: 마커를 삽입한 후, 이어서 나머지 텍스트 설명을 자연스럽게 계속 작성하세요.
    8. **노션 스타일 마크다운 적극 활용**: 굵은 글씨, 기울임체, 인용문, 목록, 테이블 등을 적절히 사용하세요.
    - **인용문(>) 사용 시점**:
     - **핵심 인사이트나 결론**: 섹션의 가장 중요한 발견사항이나 결론
     - **주요 통계나 수치**: 특별히 강조해야 할 중요한 데이터
     - **전문가 의견이나 분석**: 페르소나 관점에서의 핵심 판단이나 견해
     - **경고나 주의사항**: 독자가 반드시 알아야 할 중요한 정보
     - **해당 섹션에 대한 요약** : 해당 섹션에 대한 요약이 필요할 때 사용
     - **인용문 사용 예시**:
      - > 2024년 4분기 시장 규모가 전년 동기 대비 15% 급감하여 즉각적인 대응이 필요합니다.
      - > 데이터 분석 결과, A 전략이 B 전략 대비 ROI가 3배 높은 것으로 확인되었습니다.
    - **표 형태 데이터**: 비교나 분류가 필요한 정보는 마크다운 테이블로 구성
    - 3개 이상의 항목을 비교하거나 분류할 때 테이블 사용 권장
    - 테이블 셀 안에서도 **굵은 글씨**, *기울임체*, [SOURCE:숫자] 출처 표기 가능
    9. **출처 표기 (데이터 인덱스 번호 사용)**: 특정 정보를 참고하여 작성한 문장 바로 뒤에 [SOURCE:숫자1, 숫자2, 숫자3] 형식으로 출처를 표기하세요.


    **매우 중요 - SOURCE 번호 사용 규칙**:
    - **반드시 위 "참고 데이터"에 명시된 "[데이터 인덱스 X]" 의 X 번호만 사용하세요**
    - 예: "데이터 0", "데이터 3", "데이터 7"이 주어졌다면 → [SOURCE:0], [SOURCE:3], [SOURCE:7]만 사용 가능
    - **존재하지 않는 번호는 절대 사용하지 마세요** (예: 데이터 0~7만 있는데 [SOURCE:15] 사용 금지)
    - 반드시 숫자만 사용하고 "데이터", "문서" 등의 단어는 사용하지 마세요

    **올바른 예시** (데이터 0, 3, 8이 주어진 경우):
    - "**시장 규모가 10% 증가**했습니다. [SOURCE:8]"
    - "**매출이 5% 감소**했습니다. [SOURCE:0, 3, 8]"

    **절대 금지되는 잘못된 예시**:
    - [SOURCE:데이터 8], [SOURCE:문서 8] (단어 포함 금지)
    - [SOURCE:15] (위 참고 데이터에 없는 번호 사용 금지)

    **절대 금지 사항**:
    "추가 정보 요청", "더 많은 데이터가 필요합니다", "구체적인 데이터 부족" 등의 표현 사용 금지
    "...에 대한 추가 분석이 필요합니다" 같은 미완성 결론 제시 금지
    "데이터가 제한적입니다", "정확한 목록 작성을 위해서는..." 등의 한계 언급 금지
    **현재 확보된 데이터로 최대한 구체적이고 완전한 분석 및 결론 제시 필수**
    부족한 정보가 있어도 현재 데이터 기반으로 최선의 인사이트와 표 제공
    표 요청이 있으면 현재 데이터로 가능한 한 완전한 표 작성

    **보고서 섹션 본문**:
    """

            prompt = prompt_template.format(
                persona_instruction=persona_instruction,
                original_query=original_query,
                section_title=section_title,
                awareness_context=awareness_context,
                description=description,
                section_data_content=section_data_content
            )

        try:
            print(f"\n>> 섹션 스트리밍 시작: {section_title} (사용 인덱스: {use_indexes})")
            total_content = ""
            chunk_count = 0
            valid_content_count = 0

            async for chunk in self._astream_with_fallback(
                prompt,
                self.llm_pro,
                self.llm_pro_backup,
                self.llm_openai_4o
            ):
                chunk_count += 1
                if hasattr(chunk, 'content') and chunk.content:
                    total_content += chunk.content
                    chunk_text = chunk.content
                    valid_content_count += 1

                    print(f"- 원본 청크 {chunk_count}: {len(chunk_text)} 문자")

                    # 5자 단위로 쪼개서 전송
                    for i in range(0, len(chunk_text), 5):
                        mini_chunk = chunk_text[i:i+5]
                        yield mini_chunk

            print(f"\n>> 섹션 완료: {section_title}, 총 {chunk_count}개 원본 청크, {valid_content_count}개 유효 청크, {len(total_content)} 문자")

            if not total_content.strip() or valid_content_count == 0:
                print(f"- 섹션 스트리밍 오류 ({section_title}): No generation chunks were returned")
                raise Exception("No generation chunks were returned")

        except Exception as e:
            print(f"- 섹션 스트리밍 오류 ({section_title}): {e}")
            if "No generation chunks" in str(e) or "no valid content" in str(e).lower():
                try:
                    print(f"- OpenAI로 직접 재시도: {section_title}")
                    total_content = ""
                    chunk_count = 0

                    async for chunk in self.llm_openai_4o.astream(prompt):
                        chunk_count += 1
                        if hasattr(chunk, 'content') and chunk.content:
                            total_content += chunk.content
                            chunk_text = chunk.content
                            print(f"- OpenAI 재시도 청크 {chunk_count}: {len(chunk_text)} 문자")

                            for i in range(0, len(chunk_text), 5):
                                mini_chunk = chunk_text[i:i+5]
                                yield mini_chunk

                    print(f"- OpenAI 재시도 완료: {section_title}, {chunk_count}개 청크, {len(total_content)} 문자")

                    if not total_content.strip():
                        print(f"- OpenAI 재시도도 실패, fallback 내용 생성")
                        raise Exception("OpenAI retry also failed")

                except Exception as retry_error:
                    print(f"- OpenAI 재시도 실패: {retry_error}")
                    fallback_content = f"*'{section_title}' 섹션에 대한 상세한 분석을 생성하는 중 문제가 발생했습니다.*\n\n"
                    yield fallback_content
            else:
                error_content = f"*'{section_title}' 섹션 생성 중 오류가 발생했습니다: {str(e)}*\n\n"
                yield error_content

    async def _create_charts(self, section_data: List[SearchResult], section_title: str, generated_content: str = "", description: str = "", yield_callback=None, state: Dict[str, Any] = None):
        """⭐ 수정: 페르소나 관점을 반영하여 섹션별 선택된 데이터와 생성된 내용을 바탕으로 정확한 차트 생성"""
        print(f"  - 차트 데이터 생성: '{section_title}' (데이터 {len(section_data)}개)")
        if description:
            print(f"  - 섹션 목표: '{description}'")

        # 현재 날짜 정보 추가
        import pytz
        kst = pytz.timezone('Asia/Seoul')
        current_date_str = datetime.now(kst).strftime("%Y년 %m월 %d일")

        # 주어진 데이터로만 차트 생성 (추가 검색 없음)

        # 페르소나 정보 추출
        persona_name = state.get("persona", "기본") if state else "기본"
        default_chart_prompt = "주어진 데이터를 바탕으로 가장 명확하고 유용한 Chart.js 차트를 생성해주세요."
        persona_chart_instruction = self.personas.get(persona_name, {}).get("chart_prompt", default_chart_prompt)

        print(f"  - 차트 생성에 '{persona_name}' 페르소나 관점 적용 (차트용 프롬프트 사용)")


        # 차트 생성 context 추출
        chart_context = state.get("chart_context", {}) if state else {}
        previous_charts = chart_context.get("previous_charts", [])
        previous_sections = chart_context.get("previous_sections", [])

        async def _generate_chart_with_data(current_data: List[SearchResult], attempt: int = 1):
            """실제 차트 생성 로직 (재시도 가능)"""
            try:
                # 데이터 요약 생성
                data_summary = ""
                for i, item in enumerate(current_data):
                    source = getattr(item, 'source', 'Unknown')
                    title = getattr(item, 'title', 'No Title')
                    content = getattr(item, 'content', '')[:]
                    data_summary += f"[{i}] [{source}] {title}\n내용: {content}...\n\n"

                # 직전에 생성된 보고서 내용 추가
                context_info = ""
                if generated_content:
                    content_preview = generated_content[:] if generated_content else ""
                    context_info = f"\n**직전에 생성된 보고서 내용 (차트와 일맥상통해야 함)**:\n{content_preview}\n"

                # 이전 차트 정보 추가
                if previous_charts:
                    context_info += "\n**이전 섹션에서 생성된 차트들 (중복 방지를 위해 참고)**:\n"
                    for prev_chart in previous_charts[-2:]:  # 최근 2개만
                        chart_section = prev_chart.get("section", "")
                        chart_type = prev_chart.get("chart", {}).get("type", "")
                        chart_labels = prev_chart.get("chart", {}).get("data", {}).get("labels", [])[:]
                        context_info += f"- {chart_section}: {chart_type} 차트 (항목: {', '.join(map(str, chart_labels))}...)\n"
                    context_info += "\n"

                chart_prompt = f"""
        당신은 데이터의 **관련성**을 판단하고 **의미 있는 시각화**를 만드는 데이터 시각화 전문가입니다.

        ---

        **[PART 1: 입력 정보]**
        당신이 분석해야 할 정보는 다음과 같습니다.

        1.  **생성 목표:** '{section_title}'에 대한 차트 생성
        1.1. **섹션 목표:** '{description}'
        2.  **현재 날짜:** {current_date_str}
        3.  **페르소나:** '{persona_name}' (시도: {attempt}회차)
        4.  **참고 컨텍스트:**
            - 이전에 생성된 텍스트: {context_info}
            - 이전에 생성된 차트: (중복 방지용)
        5.  **분석할 원본 데이터:**
        {data_summary}

        ---

        **[PART 2: 수행할 명령]**
        이제, 다음 3단계 사고 프로세스에 따라 명령을 **반드시 순서대로** 수행하세요.

        **STEP 1: 상세 데이터 분석 및 차트 아이디어 구상**
        - `[PART 1]`의 정보를 바탕으로 시각화할 데이터(항목, 수치, 라벨)를 최대한 추출하고, 만들 수 있는 차트의 주제를 구체화합니다.
        - 이 과정은 아래 **`분석 템플릿`**을 채우는 방식으로 진행합니다.

        **STEP 2: 주제 일관성 검증**
        - **질문:** "STEP 1에서 구상한 차트 주제가 `[PART 1]`의 **생성 목표**인 '{section_title}'및 **섹션 목표**인 '{description}'과 직접적으로 관련이 있습니까?"
        - 이 질문에 대해 **"답변 (Yes/No)"**과 **"근거"**를 명확히 결정합니다.

        **STEP 3: 조건부 JSON 출력**
        - **만약 STEP 2의 답변이 'No'라면:** **`PLACEHOLDER_JSON`**을 출력합니다.
        - **만약 STEP 2의 답변이 'Yes'라면:** **`실제 차트 JSON`**을 생성하여 출력합니다.

        ---

        **[PART 3: 출력 형식 및 가이드]**
        최종 결과물은 아래 가이드에 따라 **'분석'**과 **'JSON'** 두 부분으로 구성되어야 합니다.

        **1. 분석 (Analysis Block):**
        - STEP 1과 STEP 2에서 당신이 생각한 과정을 아래 `분석 템플릿`에 맞춰 작성합니다.
        - Vector DB 데이터가 불분명할 수 있으니, 섹션 제목과 데이터 내용을 종합적으로 추론해야 합니다.

        분석:
        1. 상세 분석 및 아이디어 (STEP 1):
            (1) 컨텍스트 추론:
                - 섹션 제목 "{section_title}"과 **섹션 목표 "{description}"**을 보고 이 데이터가 무엇에 관한 통계인지 추론
                - 단위 정보 (원/20개, 원/50개 등)를 보고 어떤 상품/품목인지 추론
                - 출처 URL이나 문서명에서 추가 힌트 찾기
                - 추론된 품목/상품: [예: 계란, 배추, 쌀 등]

            (2) 섹션 목적: [이 섹션이 보여주려는 핵심 내용, **섹션 목표**를 기반으로 작성]

            (3) 추출 가능한 수치 데이터:
                - 항목A: [정확한 수치] + [추론된 의미] (출처: 데이터 인덱스 X)
                - 항목B: [정확한 수치] + [추론된 의미] (출처: 데이터 인덱스 Y)
                - 항목C: [정확한 수치] + [추론된 의미] (출처: 데이터 인덱스 Z)

            (4) 추출된 라벨/카테고리: [실제 연도, 월, 지역명 등]

            (5) 최적 차트 타입: [아래 지원 차트 타입에서만 선택]

            (6) 차트 구성:
                - X축: [추론된 품목명의 시간/지역/카테고리]
                - Y축: [추론된 품목명의 가격/수량 + 단위]
                - 데이터셋: [의미있는 라벨들과 해당 수치들]
                - **범례 전략**: 여러 카테고리 비교시 각각을 별도 데이터셋으로 구성!

        2. 주제 일관성 검증 (STEP 2):
            - 답변: [Yes 또는 No]
            - 근거: [예: '미생물 발효 R&D'와 '호주 기업 설립 연도'는 전혀 다른 주제이므로 No. 섹션 목표는 R&D 동향 분석인데, 기업 설립 연도는 관련성이 낮음.]

            **중요**: 관련성이 낮아도 데이터에서 숫자나 항목을 추출할 수 있다면 "부분적 Yes"로 처리하여 차트 생성 시도!


        **2. JSON (JSON Block):**
        - 위 '분석' 블록 바로 다음에, STEP 3의 규칙에 따라 아래 두 JSON 중 하나를 출력합니다.

        **[PLACEHOLDER_JSON]**
        ```json
        {{
            "type": "placeholder",
            "labels": ["'{section_title}' 관련 데이터 부족"],
            "datasets": [{{"label": "데이터 분석 불가", "data": [0], "backgroundColor": ["#FFB1C1"]}}],
            "title": "관련 데이터 부족",
            "palette": "modern",
            "options": {{"responsive": true, "plugins": {{"title": {{"display": true, "text": "현재 섹션 주제와 일치하는 데이터가 부족하여 차트를 생성할 수 없습니다."}}}}}}
        }}
        ```

        **[실제 차트 JSON 생성 가이드]**

        **중요: 지원되는 차트 타입만 사용하세요!**
        **기본 지원 차트 타입**:
        - line, bar, pie, doughnut, radar, polararea, scatter, bubble

        **확장 지원 차트 타입**:
        - area (Line 컴포넌트 + fill 옵션)
        - column (Bar 컴포넌트)
        - donut (Doughnut 컴포넌트)
        - polar (PolarArea 컴포넌트)
        - horizontalbar (Bar 컴포넌트)
        - stacked (Bar 컴포넌트 + stack 옵션)
        - mixed (Line 컴포넌트)
        - funnel (Bar 컴포넌트)
        - waterfall (Bar 컴포넌트)
        - gauge (Doughnut 컴포넌트)
        - timeseries (Line 컴포넌트)
        - timeline (Line 컴포넌트)
        - gantt (Bar 컴포넌트)
        - multiline (Line 컴포넌트)
        - groupedbar (Bar 컴포넌트)
        - stackedarea (Line 컴포넌트)
        - combo (Line 컴포넌트)
        - heatmap (Bar 컴포넌트)
        - treemap (Bar 컴포넌트)
        - sankey (Bar 컴포넌트)
        - candlestick (Line 컴포넌트)
        - violin (Bar 컴포넌트)
        - boxplot (Bar 컴포넌트)

        **지원하지 않는 타입들 (절대 사용 금지)**:
        - groupedbarchart (올바른 타입: groupedbar)
        - 기타 존재하지 않는 타입명들

        **선택 기준**:
        - 카테고리별 비교 → **bar**, **column**, **horizontalbar**
        - 그룹화된 비교 → **groupedbar**, **stacked**
        - 시간 변화 → **line**, **timeseries**, **timeline**, **area**
        - 비율/구성 → **pie**, **doughnut**, **donut**
        - 다차원 비교 → **radar**, **polar**
        - 관계 분석 → **scatter**, **bubble**
        - 복합 차트 → **mixed**, **combo**
        - 특수 목적 → **funnel**, **waterfall**, **gauge**, **heatmap**

        **올바른 색상 및 라벨링 규칙 - 범례 문제 해결**:

        **중요: 카테고리별 비교 시 반드시 다중 데이터셋 사용!**

        **잘못된 방법 (범례 1개만 나옴)**:
        ```json
        {{
            "type": "bar",
            "data": {{
                "labels": ["미국", "일본"],
                "datasets": [{{
                    "label": "2023년 스낵 시장 규모",
                    "data": [71000, 11330],
                    "backgroundColor": ["#4F46E5", "#7C3AED"]
                }}]
            }}
        }}
        ```

        **올바른 방법 (각 카테고리별 범례 표시)**:
        ```json
        {{
            "type": "bar",
            "data": {{
                "labels": ["시장 규모"],
                "datasets": [
                    {{
                        "label": "미국",
                        "data": [71000],
                        "backgroundColor": "#4F46E5"
                    }},
                    {{
                        "label": "일본",
                        "data": [11330],
                        "backgroundColor": "#7C3AED"
                    }}
                ]
            }}
        }}
        ```

        **데이터 구조 변환 규칙**:

        **지역/국가별 비교 → 각각을 별도 데이터셋으로**
        **시계열 비교 → 각 시점을 별도 데이터셋으로**
        **제품/카테고리 비교 → 각각을 별도 데이터셋으로**

        **다양한 비교 케이스별 올바른 구조**:

        **1. 지역별 비교**:
        ```json
        {{
            "type": "bar",
            "data": {{
                "labels": ["시장 규모"],
                "datasets": [
                    {{"label": "미국", "data": [값1], "backgroundColor": "#4F46E5"}},
                    {{"label": "중국", "data": [값2], "backgroundColor": "#7C3AED"}},
                    {{"label": "일본", "data": [값3], "backgroundColor": "#EC4899"}}
                ]
            }}
        }}
        ```

        **2. 연도별 비교**:
        ```json
        {{
            "type": "bar",
            "data": {{
                "labels": ["성장률"],
                "datasets": [
                    {{"label": "2022년", "data": [값1], "backgroundColor": "#4F46E5"}},
                    {{"label": "2023년", "data": [값2], "backgroundColor": "#7C3AED"}},
                    {{"label": "2024년", "data": [값3], "backgroundColor": "#EC4899"}}
                ]
            }}
        }}
        ```

        **3. 제품별 비교**:
        ```json
        {{
            "type": "bar",
            "data": {{
                "labels": ["매출"],
                "datasets": [
                    {{"label": "제품A", "data": [값1], "backgroundColor": "#4F46E5"}},
                    {{"label": "제품B", "data": [값2], "backgroundColor": "#7C3AED"}},
                    {{"label": "제품C", "data": [값3], "backgroundColor": "#EC4899"}}
                ]
            }}
        }}
        ```

        **4. 단일 항목의 시간 변화 (이 경우만 단일 데이터셋)**:
        ```json
        {{
            "type": "line",
            "data": {{
                "labels": ["2021", "2022", "2023", "2024"],
                "datasets": [{{
                    "label": "성장 추세",
                    "data": [100, 120, 150, 180],
                    "borderColor": "#4F46E5",
                    "backgroundColor": "#4F46E520"
                }}]
            }}
        }}
        ```

        **차트 타입별 데이터 구조 가이드**:

        **stacked 차트**:
        ```json
        {{
            "type": "stacked",
            "data": {{
                "labels": ["Q1", "Q2", "Q3"],
                "datasets": [
                    {{"label": "제품A", "data": [10, 20, 30], "backgroundColor": "#4F46E5"}},
                    {{"label": "제품B", "data": [15, 25, 35], "backgroundColor": "#EC4899"}}
                ]
            }}
        }}
        ```

        **timeseries/timeline 차트**:
        ```json
        {{
            "type": "timeseries",
            "data": {{
                "labels": ["2023-01", "2023-02", "2023-03"],
                "datasets": [{{
                    "label": "월별 데이터",
                    "data": [100, 150, 200],
                    "borderColor": "#4F46E5",
                    "backgroundColor": "#4F46E520"
                }}]
            }}
        }}
        ```

        **추천 색상 팔레트**:
        - 메인: ["#4F46E5", "#7C3AED", "#EC4899", "#EF4444", "#F59E0B"]
        - 보조: ["#06B6D4", "#10B981", "#84CC16", "#F97316", "#8B5CF6"]

        **차트 생성 우선 원칙 - PLACEHOLDER 금지!**:

        **1단계: 직접 수치 데이터 활용**
        - 문서에서 금액, 개수, 비율, 점수 등 구체적 숫자가 있으면 즉시 차트화
        - 예: "2023년 113억 3,070만 달러" → bar 차트로 연도별 수치

        **2단계: 카테고리/항목 개수 차트화**
        - 여러 항목이 나열되어 있으면 항목별 개수/빈도로 차트 생성
        - 예: "일본 스낵, 저염 스낵, 세이버리 비스킷, 팝콘, 프레첼" → 5개 항목 bar 차트

        **3단계: 지역/시간 정보 활용**
        - 지역명 언급 → 지역별 분포 차트
        - 연도/날짜 언급 → 시계열 차트
        - 예: "미국, 중국, 동남아" 언급 → 3개 지역 pie 차트

        **4단계: 추론 가능한 데이터 생성**
        - 시장 규모 언급 → 상대적 크기 비교 차트
        - 성장률 언급 → 증가 추세 라인 차트
        - 점유율 언급 → 파이 차트

        **5단계: 메타 정보 차트화**
        - 문서 개수, 언급 횟수, 키워드 빈도
        - 예: "3개 문서에서 언급" → 문서별 언급 횟수 차트

        **창조적 차트 생성 예시**:
        ```
        - "일본, 미국, 동남아시아 시장" → {{"labels": ["일본", "미국", "동남아"], "data": [1, 1, 1]}}
        - "2019년 이후 연평균 3.5% 감소" → {{"labels": ["2019", "2020", "2021", "2022"], "data": [100, 96.5, 93.2, 90.0]}}
        - "5가지 트렌드 언급" → {{"labels": ["트렌드1", "트렌드2", "트렌드3", "트렌드4", "트렌드5"], "data": [1, 1, 1, 1, 1]}}
        ```

        **PLACEHOLDER는 다음 경우에만 (매우 예외적)**:
        - 섹션 제목과 데이터가 완전히 무관한 경우만 (예: "자동차 산업" 섹션인데 데이터가 "요리 레시피"인 경우)
        - 데이터가 완전히 비어있거나 의미없는 텍스트만 있는 경우

        **데이터 추출 체크리스트**:
        숫자가 하나라도 있는가? → 차트 생성!
        항목이 2개 이상 나열되어 있는가? → 차트 생성!
        지역/국가명이 언급되는가? → 차트 생성!
        연도/시기가 언급되는가? → 차트 생성!
        비교 표현이 있는가? (더 크다, 증가, 감소 등) → 차트 생성!

        **표준 JSON 형식**:
        {{
            "type": "STEP1분석_기반_차트타입",
            "data": {{
                "labels": ["STEP1추출_실제라벨1", "실제라벨2", "실제라벨3"],
                "datasets": [{{
                    "label": "STEP1정의_데이터셋명",
                    "data": [STEP1추출_실제수치1, 실제수치2, 실제수치3],
                    "backgroundColor": ["#4F46E5", "#7C3AED", "#EC4899", "#EF4444", "#F59E0B"],
                    "borderColor": ["#4F46E5", "#7C3AED", "#EC4899", "#EF4444", "#F59E0B"],
                    "borderWidth": 1
                }}]
            }},
            "options": {{
                "responsive": true,
                "plugins": {{
                    "title": {{
                        "display": true,
                        "text": "{section_title}"
                    }},
                    "legend": {{
                        "display": true,
                        "position": "top"
                    }}
                }},
                "scales": {{
                    "y": {{
                        "beginAtZero": true,
                        "title": {{
                            "display": true,
                            "text": "값 (단위)"
                        }}
                    }},
                    "x": {{
                        "title": {{
                            "display": true,
                            "text": "카테고리"
                        }}
                    }}
                }}
            }}
        }}
        """

                response = await self._invoke_with_fallback(
                    chart_prompt,
                    self.llm_flash,
                    self.llm_flash_backup,
                    self.llm_openai_mini
                )
                response_text = response.content.strip()

                # JavaScript 함수 제거 헬퍼 함수 정의
                def clean_js_functions(json_str):
                    """JSON에서 JavaScript 함수 코드를 더 견고하게 제거"""
                    import re

                    # 1. 가장 안전한 방법: callbacks 객체 전체를 빈 객체로 교체
                    # 중첩된 중괄호까지 모두 포함하여 제거
                    def find_and_replace_callbacks(text):
                        """callbacks 객체를 찾아서 빈 객체로 교체"""
                        pattern = r'"callbacks"\s*:\s*\{'
                        match = re.search(pattern, text)
                        if not match:
                            return text

                        start_pos = match.start()
                        brace_pos = text.find('{', match.end() - 1)

                        if brace_pos == -1:
                            return text

                        # 중괄호 균형 맞추기
                        brace_count = 1
                        i = brace_pos + 1

                        while i < len(text) and brace_count > 0:
                            if text[i] == '{':
                                brace_count += 1
                            elif text[i] == '}':
                                brace_count -= 1
                            i += 1

                        if brace_count == 0:
                            # callbacks 객체 전체를 빈 객체로 교체
                            before = text[:start_pos]
                            after = text[i:]
                            return before + '"callbacks": {}' + after

                        return text

                    # 2. callbacks 객체 교체
                    json_str = find_and_replace_callbacks(json_str)

                    # 3. 남은 function 패턴들 제거
                    # function(...) { ... } 패턴을 null로 교체
                    json_str = re.sub(r'function\s*\([^)]*\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', 'null', json_str, flags=re.DOTALL)

                    # 4. 키-값에서 function으로 시작하는 값들 제거
                    json_str = re.sub(r':\s*function\s*\([^)]*\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', ': null', json_str, flags=re.DOTALL)

                    # 5. 잘못된 JSON 구조 정리
                    # null 뒤에 오는 잘못된 코드 패턴 제거
                    json_str = re.sub(r'null\s+[^,}\]]*(?:if|let|var|return|context)[^,}\]]*', 'null', json_str, flags=re.DOTALL)

                    # 6. 여러 줄에 걸친 함수 본문 정리
                    json_str = re.sub(r'null\s*\n\s*if\s*\([^)]*\)\s*\{[^}]*\}\s*return[^;}]*;?\s*\}?', 'null', json_str, flags=re.DOTALL)

                    # 7. 남은 JavaScript 코드 조각들 제거
                    json_str = re.sub(r'if\s*\([^)]*\)\s*\{[^}]*\}', '', json_str, flags=re.DOTALL)
                    json_str = re.sub(r'return\s+[^;}]*;?', '', json_str, flags=re.DOTALL)
                    json_str = re.sub(r'let\s+\w+\s*=\s*[^;]*;', '', json_str, flags=re.DOTALL)
                    json_str = re.sub(r'var\s+\w+\s*=\s*[^;]*;', '', json_str, flags=re.DOTALL)

                    # 8. 빈 문자열이나 null 값들 정리
                    json_str = re.sub(r',\s*null\s*,', ',', json_str)
                    json_str = re.sub(r',\s*null\s*}', '}', json_str)
                    json_str = re.sub(r'{\s*null\s*,', '{', json_str)

                    # 9. 불완전한 구조 정리
                    json_str = re.sub(r'\s*\n\s*\n\s*', '\n', json_str)  # 빈 줄 정리
                    json_str = re.sub(r',\s*}', '}', json_str)  # trailing comma 제거
                    json_str = re.sub(r',\s*]', ']', json_str)  # trailing comma 제거

                    return json_str

                # 간단하고 정확한 JSON 추출 함수
                def extract_json_simple(text):
                    """간단하고 정확한 JSON 추출"""
                    # 1. ```json 블록에서 추출
                    if "```json" in text:
                        start = text.find("```json") + 7
                        end = text.find("```", start)
                        if end != -1:
                            return text[start:end].strip()

                    # 2. JSON: 다음에서 추출
                    if "JSON:" in text:
                        start = text.find("JSON:") + 5
                        # 첫 번째 { 찾기
                        json_start = text.find("{", start)
                        if json_start == -1:
                            return None

                        # 균형잡힌 } 찾기
                        brace_count = 0
                        for i in range(json_start, len(text)):
                            if text[i] == '{':
                                brace_count += 1
                            elif text[i] == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    return text[json_start:i+1]

                    # 3. 첫 번째 { }블록 추출
                    json_start = text.find("{")
                    if json_start == -1:
                        return None

                    brace_count = 0
                    for i in range(json_start, len(text)):
                        if text[i] == '{':
                            brace_count += 1
                        elif text[i] == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                return text[json_start:i+1]

                    return None

                # COT 응답에서 JSON 추출
                try:
                    print(f"  - 원본 LLM 응답 (처음 500자): {response_text[:500]}...")
                    print(f"  - 원본 응답 길이: {len(response_text)}자")

                    # 간단한 JSON 추출 적용
                    json_part = extract_json_simple(response_text)
                    if not json_part:
                        print(f"  - JSON 추출 실패: JSON 블록을 찾을 수 없음")
                        raise ValueError("JSON 블록을 찾을 수 없음")

                    print(f"  - JSON 추출 성공: {len(json_part)}자")

                    print(f"  - 추출된 JSON 파트: {json_part[:300]}...")

                    # JavaScript 함수 제거 (JSON 파싱 전에 실행)
                    cleaned_json = clean_js_functions(json_part)
                    print(f"  - JavaScript 함수 제거 후: {cleaned_json[:300]}...")

                    # JSON 파싱
                    chart_response = json.loads(cleaned_json)

                    # 리스트 형태의 JSON 응답 처리
                    if isinstance(chart_response, list) and len(chart_response) > 0:
                        print(f"  - JSON 파싱 성공: 리스트 형태 ({len(chart_response)}개 항목), 첫 번째 항목 사용")
                        chart_response = chart_response[0]  # 첫 번째 항목 사용
                    elif isinstance(chart_response, dict):
                        print(f"  - JSON 파싱 성공: 딕셔너리 형태")
                    else:
                        raise ValueError(f"올바르지 않은 JSON 형식: {type(chart_response)}")

                    print(f"  - 차트 타입: {chart_response.get('type', 'unknown')}")
                    print(f"  - insufficient_data 여부: {chart_response.get('insufficient_data', False)}")

                    if chart_response.get('insufficient_data', False):
                        print(f"  - 부족한 데이터 정보: {chart_response.get('missing_info', 'N/A')}")
                        print(f"  - 제안된 검색어: {chart_response.get('suggested_search_query', 'N/A')}")

                    # 이제 추가 검색은 보고서 구조 단계에서 처리됨

                    # >> 정상적인 차트 데이터인 경우
                    elif "type" in chart_response and "data" in chart_response:
                        # 필수 필드 검증
                        datasets = chart_response.get("data", {}).get("datasets", [])
                        if datasets and len(datasets) > 0:
                            data_points = datasets[0].get("data", [])
                            if len(data_points) < 2:
                                print(f"  - 경고: 차트 데이터 포인트가 부족함 ({len(data_points)}개)")

                        # 콜백 함수 제거 (프론트엔드 오류 방지)
                        def remove_callbacks(obj):
                            if isinstance(obj, dict):
                                # 콜백 함수 관련 키들 제거
                                callback_keys = ['callback', 'callbacks', 'generateLabels']
                                for key in list(obj.keys()):
                                    if key in callback_keys:
                                        del obj[key]
                                    elif isinstance(obj[key], str) and 'function' in obj[key]:
                                        del obj[key]
                                    else:
                                        remove_callbacks(obj[key])
                            elif isinstance(obj, list):
                                for item in obj:
                                    remove_callbacks(item)

                        remove_callbacks(chart_response)

                        print(f"  - 차트 생성 성공: {chart_response['type']} 타입, {len(datasets)}개 데이터셋 (시도 {attempt})")
                        yield {
                            "type": "chart",
                            "data": chart_response
                        }
                        return
                    else:
                        raise ValueError("올바르지 않은 JSON 형식")

                except (json.JSONDecodeError, ValueError) as e:
                    print(f"  - 차트 JSON 파싱 실패 (시도 {attempt}): {e}")
                    print(f"  - 추출된 JSON 길이: {len(json_part)}자")
                    print(f"  - JSON 시작: {json_part[:200]}...")
                    print(f"  - JSON 끝: ...{json_part[-200:]}")

                    # 간단한 재시도: 전체 응답에서 다시 JSON 추출 시도
                    retry_success = False
                    try:
                        print(f"  - JSON 파싱 재시도 중...")
                        # 전체 응답에서 완전한 JSON 블록 찾기
                        json_start = response_text.find("{")
                        if json_start != -1:
                            brace_count = 0
                            for i in range(json_start, len(response_text)):
                                if response_text[i] == '{':
                                    brace_count += 1
                                elif response_text[i] == '}':
                                    brace_count -= 1
                                    if brace_count == 0:
                                        retry_json = response_text[json_start:i+1]
                                        cleaned_retry = clean_js_functions(retry_json)
                                        chart_response = json.loads(cleaned_retry)

                                        # 리스트 형태의 JSON 응답 처리
                                        if isinstance(chart_response, list) and len(chart_response) > 0:
                                            print(f"  - 재시도: 리스트 형태 ({len(chart_response)}개 항목), 첫 번째 항목 사용")
                                            chart_response = chart_response[0]

                                        print(f"  - 재시도 JSON 파싱 성공! ({len(retry_json)}자)")
                                        retry_success = True
                                        break
                    except Exception as retry_e:
                        print(f"  - JSON 파싱 재시도도 실패: {retry_e}")

                    # 재시도 성공한 경우 처리
                    if retry_success:
                        print(f"  - JSON 파싱 재시도 성공! 차트 생성 진행")

                        # insufficient_data 체크
                        if chart_response.get('insufficient_data', False):
                            print(f"  - 부족한 데이터 정보: {chart_response.get('missing_info', 'N/A')}")
                            print(f"  - 제안된 검색어: {chart_response.get('suggested_search_query', 'N/A')}")
                            # 이제 추가 검색은 보고서 구조 단계에서 처리됨

                        # 정상적인 차트 데이터인 경우
                        elif "type" in chart_response and "data" in chart_response:
                            # 필수 필드 검증
                            datasets = chart_response.get("data", {}).get("datasets", [])
                            if datasets and len(datasets) > 0:
                                data_points = datasets[0].get("data", [])
                                if len(data_points) < 2:
                                    print(f"  - 경고: 차트 데이터 포인트가 부족함 ({len(data_points)}개)")

                            # 콜백 함수 제거 (프론트엔드 오류 방지)
                            def remove_callbacks(obj):
                                if isinstance(obj, dict):
                                    # 콜백 함수 관련 키들 제거
                                    callback_keys = ['callback', 'callbacks', 'generateLabels']
                                    for key in list(obj.keys()):
                                        if key in callback_keys:
                                            del obj[key]
                                        elif isinstance(obj[key], str) and 'function' in obj[key]:
                                            del obj[key]
                                        else:
                                            remove_callbacks(obj[key])
                                elif isinstance(obj, list):
                                    for item in obj:
                                        remove_callbacks(item)

                            remove_callbacks(chart_response)

                            print(f"  - 차트 생성 성공: {chart_response['type']} 타입, {len(datasets)}개 데이터셋 (재시도 성공)")
                            yield {
                                "type": "chart",
                                "data": chart_response
                            }
                            return
                        else:
                            print(f"  - 재시도 성공했지만 올바르지 않은 JSON 형식")

                    print(f"  - JSON 파싱 재시도도 실패, fallback 차트로 진행")

                    # 최종 fallback 차트
                    yield {
                        "type": "chart",
                        "data": {
                            "type": "bar",
                            "data": {
                                "labels": [f"{section_title} 관련 데이터"],
                                "datasets": [{
                                    "label": "정보 수집 상태",
                                    "data": [1],
                                    "backgroundColor": "rgba(255, 193, 7, 0.6)",
                                    "borderColor": "rgba(255, 193, 7, 1)",
                                    "borderWidth": 1
                                }]
                            },
                            "options": {
                                "responsive": True,
                                "plugins": {
                                    "title": {
                                        "display": True,
                                        "text": f"{section_title} - 데이터 분석 중"
                                    }
                                },
                                "scales": {
                                    "y": {
                                        "beginAtZero": True,
                                        "max": 2,
                                        "ticks": {
                                            "stepSize": 1
                                        }
                                    }
                                }
                            }
                        }
                    }

            except Exception as e:
                print(f"  - 차트 생성 전체 오류 (시도 {attempt}): {e}")
                yield {
                    "type": "chart",
                    "data": {
                        "type": "bar",
                        "data": {
                            "labels": ["시스템 오류"],
                            "datasets": [{
                                "label": "처리 상태",
                                "data": [0],
                                "backgroundColor": "rgba(220, 53, 69, 0.6)",
                                "borderColor": "rgba(220, 53, 69, 1)",
                                "borderWidth": 1
                            }]
                        },
                        "options": {
                            "responsive": True,
                            "plugins": {
                                "title": {
                                    "display": True,
                                    "text": "차트 생성 중 오류 발생"
                                }
                            }
                        }
                    }
                }

        # >> 메인 로직 실행
        async for result in _generate_chart_with_data(section_data, attempt=1):
            yield result
