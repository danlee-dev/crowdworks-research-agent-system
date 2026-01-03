# 백업 체크리스트

## ✅ 이미 백업한 것 (완료)

| 파일 | 크기 | 내용 | 상태 |
|------|------|------|------|
| `env_backup.tar.gz` | 2KB | 환경 변수 (.env) | ✅ 완료 |
| `db_backup.tar.gz` | 59.1MB | PostgreSQL + Neo4j data/ | ✅ 완료 |
| `elasticsearch_backup.tar.gz` | 1.85GB | Elasticsearch 인덱스 | ✅ 완료 |
| `neo4j_volume_backup.tar.gz` | 92.3MB | Neo4j volume | ✅ 완료 |

**총 백업 크기**: ~2GB

---

## ⚠️ 백업 안 된 중요 데이터

### 🔴 매우 중요 - 반드시 백업 권장

#### 1. elasticsearch/embedding_datas/ (1.5GB)
- **내용**: 벡터 임베딩 데이터
- **중요도**: 🔴 **매우 높음**
- **이유**:
  - 재생성에 많은 시간과 비용 (API 호출) 소요
  - Sentence Transformers로 생성한 임베딩
  - RAG 시스템의 핵심 데이터
- **백업 명령어**:
  ```bash
  tar -czf elasticsearch_embedding_backup.tar.gz elasticsearch/embedding_datas/
  ```

#### 2. crawler/ReferencePDF/ (155MB)
- **내용**: 참조 PDF 문서 (33개)
- **중요도**: 🔴 **높음**
- **이유**:
  - Knowledge Compiler로 처리된 원본 문서
  - 재수집 어려울 수 있음
  - 농림축산식품부, 농촌진흥청 보고서 등
- **백업 명령어**:
  ```bash
  tar -czf crawler_reference_pdf_backup.tar.gz crawler/ReferencePDF/
  ```

#### 3. elasticsearch/preprocessed_datas/ (145MB)
- **내용**: 전처리된 문서 데이터
- **중요도**: 🟠 **중상**
- **이유**:
  - PDF → JSON 전처리 결과
  - 재생성 가능하지만 시간 소요
- **백업 명령어**:
  ```bash
  tar -czf elasticsearch_preprocessed_backup.tar.gz elasticsearch/preprocessed_datas/
  ```

---

### 🟡 중간 중요도 - 선택적 백업

#### 4. Neo4J/report_data/ (42MB)
- **내용**: Neo4j 리포트 데이터
- **중요도**: 🟡 **중간**
- **이유**: Neo4j 볼륨에 이미 포함되어 있을 가능성
- **백업 명령어**:
  ```bash
  tar -czf neo4j_report_data_backup.tar.gz Neo4J/report_data/
  ```

#### 5. Neo4J/import/ (5.1MB)
- **내용**: CSV 원본 데이터
- **중요도**: 🟡 **중간**
- **이유**:
  - Neo4j 데이터 로드용 CSV
  - PostgreSQL에 이미 데이터 있음
- **백업 명령어**:
  ```bash
  tar -czf neo4j_import_backup.tar.gz Neo4J/import/
  ```

#### 6. crawler_rdb/data/ (7.7MB)
- **내용**: 국가표준식품성분표 Excel
- **중요도**: 🟡 **중간**
- **이유**: PostgreSQL에 이미 ETL됨
- **백업 명령어**:
  ```bash
  tar -czf crawler_rdb_data_backup.tar.gz crawler_rdb/data/
  ```

---

### 🟢 낮은 중요도 - 백업 불필요

#### 7. evaluation_results/ (4.8MB)
- **내용**: 성능 평가 결과
- **중요도**: 🟢 **낮음**
- **이유**: 재실행 가능

#### 8. logs/ (112KB)
- **내용**: 로그 파일
- **중요도**: 🟢 **매우 낮음**
- **이유**: 디버깅용, 필요 시 재생성

#### 9. temp_img/ (4KB)
- **내용**: 임시 이미지
- **중요도**: 🟢 **매우 낮음**

---

## 📋 권장 백업 전략

### 최소 백업 (필수만)

**반드시 백업해야 할 것**:
1. ✅ env_backup.tar.gz (완료)
2. ✅ db_backup.tar.gz (완료)
3. ✅ elasticsearch_backup.tar.gz (완료)
4. ✅ neo4j_volume_backup.tar.gz (완료)
5. ⏳ elasticsearch_embedding_backup.tar.gz (1.5GB) - **강력 권장**

**총 크기**: ~3.5GB

---

### 완전 백업 (권장)

**위 5개 + 추가**:
6. ⏳ crawler_reference_pdf_backup.tar.gz (155MB)
7. ⏳ elasticsearch_preprocessed_backup.tar.gz (145MB)
8. ⏳ neo4j_report_data_backup.tar.gz (42MB)
9. ⏳ neo4j_import_backup.tar.gz (5.1MB)
10. ⏳ crawler_rdb_data_backup.tar.gz (7.7MB)

**총 크기**: ~3.9GB

---

## 🚀 추가 백업 실행 스크립트

### 필수 항목만 (1.5GB)

```bash
cd /root/workspace/crowdworks/crowdworks-multiagent-system

# elasticsearch 임베딩 데이터 백업
echo "[1/1] Elasticsearch 임베딩 데이터 백업 중..."
tar -czf elasticsearch_embedding_backup.tar.gz elasticsearch/embedding_datas/

echo "✓ 필수 백업 완료"
ls -lh elasticsearch_embedding_backup.tar.gz
```

### 완전 백업 (전체)

```bash
cd /root/workspace/crowdworks/crowdworks-multiagent-system

echo "=== 추가 백업 시작 ==="

# 1. Elasticsearch 임베딩 (1.5GB)
echo "[1/5] Elasticsearch 임베딩 백업 중..."
tar -czf elasticsearch_embedding_backup.tar.gz elasticsearch/embedding_datas/

# 2. 참조 PDF (155MB)
echo "[2/5] 참조 PDF 백업 중..."
tar -czf crawler_reference_pdf_backup.tar.gz crawler/ReferencePDF/

# 3. 전처리 데이터 (145MB)
echo "[3/5] 전처리 데이터 백업 중..."
tar -czf elasticsearch_preprocessed_backup.tar.gz elasticsearch/preprocessed_datas/

# 4. Neo4j 관련 (42MB + 5.1MB)
echo "[4/5] Neo4j 데이터 백업 중..."
tar -czf neo4j_additional_backup.tar.gz Neo4J/report_data/ Neo4J/import/

# 5. crawler_rdb 데이터 (7.7MB)
echo "[5/5] crawler_rdb 데이터 백업 중..."
tar -czf crawler_rdb_data_backup.tar.gz crawler_rdb/data/

echo ""
echo "=== 백업 완료 ==="
echo "생성된 파일:"
ls -lh *_backup.tar.gz | grep -v "env_backup\|db_backup\|elasticsearch_backup.tar.gz\|neo4j_volume_backup"
```

---

## 📥 로컬 다운로드

```bash
# 로컬 PC에서 실행 (필수만)
scp root@서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/elasticsearch_embedding_backup.tar.gz ~/Downloads/

# 로컬 PC에서 실행 (완전 백업)
scp root@서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/*_backup.tar.gz ~/Downloads/
```

---

## 🔄 복원 시 위치

| 백업 파일 | 복원 위치 |
|-----------|----------|
| elasticsearch_embedding_backup.tar.gz | `elasticsearch/embedding_datas/` |
| crawler_reference_pdf_backup.tar.gz | `crawler/ReferencePDF/` |
| elasticsearch_preprocessed_backup.tar.gz | `elasticsearch/preprocessed_datas/` |
| neo4j_additional_backup.tar.gz | `Neo4J/report_data/`, `Neo4J/import/` |
| crawler_rdb_data_backup.tar.gz | `crawler_rdb/data/` |

---

## 💡 판단 기준

### 백업해야 하는가?

**YES - 반드시 백업**:
- 재생성 비용이 높음 (시간, API 비용)
- 원본 데이터 (수집 어려움)
- 시스템 핵심 데이터

**NO - 백업 불필요**:
- 쉽게 재생성 가능
- 로그, 임시 파일
- 이미 다른 곳에 백업됨

### 용량 vs 중요도

| 중요도 | 용량 제한 | 예시 |
|--------|----------|------|
| 매우 중요 | 제한 없음 | 임베딩 데이터 (1.5GB도 백업) |
| 중요 | ~200MB | PDF 원본 (155MB 백업) |
| 보통 | ~50MB | 설정 파일, 소스 CSV |
| 낮음 | 백업 안 함 | 로그, 평가 결과 |

---

## ✅ 최종 권장사항

**시간이 없다면** (최소):
- ✅ elasticsearch_embedding_backup.tar.gz (1.5GB) - **필수!**

**시간이 있다면** (권장):
- ✅ 위 + crawler_reference_pdf_backup.tar.gz (155MB)
- ✅ 위 + elasticsearch_preprocessed_backup.tar.gz (145MB)

**완벽주의자라면** (완전):
- ✅ 위 전부 + Neo4j, crawler_rdb 데이터
