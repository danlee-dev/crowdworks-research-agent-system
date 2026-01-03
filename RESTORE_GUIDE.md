# 백업 복원 가이드

## 백업 파일 설명

새 서버에서 git clone 후 다음 백업 파일들을 사용하여 시스템을 복원합니다.

### 필수 백업 파일 (4개)

| 파일명 | 크기 | 내용 | 복원 위치 |
|--------|------|------|-----------|
| `env_backup.tar.gz` | 2KB | API 키, 비밀번호 등 환경 변수 | 프로젝트 루트 및 하위 디렉토리 |
| `db_backup.tar.gz` | 59.1MB | PostgreSQL + Neo4j 데이터 | `postgresql/`, `Neo4J/data/` |
| `elasticsearch_backup.tar.gz` | 1.85GB | Elasticsearch 인덱스 | Docker volume |
| `neo4j_volume_backup.tar.gz` | 92.3MB | Neo4j volume 데이터 | Docker volume |

### 선택적 백업 파일 (권장)

| 파일명 | 크기 | 내용 | 복원 위치 |
|--------|------|------|-----------|
| `elasticsearch_embedding_backup.tar.gz` | 1.5GB | 벡터 임베딩 데이터 (권장) | `elasticsearch/embedding_datas/` |
| `crawler_reference_pdf_backup.tar.gz` | 155MB | 참조 PDF 문서 | `crawler/ReferencePDF/` |
| `elasticsearch_preprocessed_backup.tar.gz` | 145MB | 전처리 데이터 | `elasticsearch/preprocessed_datas/` |

---

## 복원 순서

### 1단계: 새 서버에 Git Clone

```bash
cd /root/workspace/crowdworks/
git clone <your-repository-url> crowdworks-multiagent-system
cd crowdworks-multiagent-system
```

### 2단계: 백업 파일을 서버에 업로드

로컬 PC에서 실행:
```bash
# 백업 파일들을 새 서버로 업로드
scp env_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/
scp db_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/
scp elasticsearch_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/
scp neo4j_volume_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/
```

### 3단계: 환경 변수 복원

```bash
cd /root/workspace/crowdworks/crowdworks-multiagent-system

# 환경 변수 압축 해제
tar -xzf env_backup.tar.gz

# 복원된 파일 확인
ls -la .env
ls -la */.env
```

**⚠️ 중요: 새 서버에서는 반드시 API 키와 비밀번호를 교체하세요!**

```bash
# .env 파일 수정
nano .env

# 교체해야 할 항목:
# - OPENAI_API_KEY
# - GEMINI_API_KEY_1, GEMINI_API_KEY_2
# - SERPER_API_KEY
# - POSTGRES_PASSWORD
# - NEO4J_PASSWORD
# - ELASTICSEARCH_PASSWORD
```

### 4단계: Docker Volumes 생성

```bash
# Elasticsearch volume 생성
docker volume create elasticsearch_es_data

# Neo4j volume 생성
docker volume create neo4j_data_original
```

### 5단계: Docker Volumes에 데이터 복원

```bash
# Elasticsearch 데이터 복원
docker run --rm -v elasticsearch_es_data:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/elasticsearch_backup.tar.gz"

# Neo4j volume 데이터 복원
docker run --rm -v neo4j_data_original:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/neo4j_volume_backup.tar.gz"
```

### 6단계: 데이터베이스 디렉토리 복원

```bash
# PostgreSQL과 Neo4j 데이터 디렉토리 복원
tar -xzf db_backup.tar.gz

# 복원 확인
ls -la postgresql/
ls -la Neo4J/data/
```

### 7단계: Docker Compose 실행

```bash
# 모든 서비스 시작
docker-compose up -d

# 로그 확인
docker-compose logs -f
```

### 8단계: 복원 확인

```bash
# 데이터베이스 연결 확인
docker exec -it crowdworks_db psql -U crowdworks_user -d crowdworks_db -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';"

# Elasticsearch 확인
curl http://localhost:9200/_cat/indices?v

# Neo4j 확인 (브라우저에서 http://새서버IP:7474)
```

---

## 자동 복원 스크립트

위 과정을 자동화한 스크립트입니다:

```bash
#!/bin/bash
# restore.sh

set -e  # 에러 발생 시 중단

BACKUP_DIR="/root/workspace/crowdworks/crowdworks-multiagent-system"
cd $BACKUP_DIR

echo "=== 백업 복원 시작 ==="

# 1. 환경 변수 복원
echo "[1/5] 환경 변수 복원 중..."
tar -xzf env_backup.tar.gz
echo "✓ 환경 변수 복원 완료"
echo "⚠️  WARNING: .env 파일의 API 키와 비밀번호를 반드시 교체하세요!"

# 2. Docker volumes 생성
echo "[2/5] Docker volumes 생성 중..."
docker volume create elasticsearch_es_data || echo "elasticsearch_es_data 이미 존재"
docker volume create neo4j_data_original || echo "neo4j_data_original 이미 존재"
echo "✓ Docker volumes 생성 완료"

# 3. Docker volumes에 데이터 복원
echo "[3/5] Elasticsearch 데이터 복원 중..."
docker run --rm -v elasticsearch_es_data:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/elasticsearch_backup.tar.gz"
echo "✓ Elasticsearch 데이터 복원 완료"

echo "[4/5] Neo4j volume 데이터 복원 중..."
docker run --rm -v neo4j_data_original:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/neo4j_volume_backup.tar.gz"
echo "✓ Neo4j volume 데이터 복원 완료"

# 4. 데이터베이스 디렉토리 복원
echo "[5/5] 데이터베이스 디렉토리 복원 중..."
tar -xzf db_backup.tar.gz
echo "✓ 데이터베이스 디렉토리 복원 완료"

echo ""
echo "=== 백업 복원 완료 ==="
echo ""
echo "다음 단계:"
echo "1. .env 파일의 API 키와 비밀번호를 교체하세요"
echo "2. docker-compose up -d 명령으로 서비스를 시작하세요"
echo "3. docker-compose logs -f 명령으로 로그를 확인하세요"
echo ""
echo "백업 파일 정리 (선택):"
echo "  rm -f *.tar.gz"
