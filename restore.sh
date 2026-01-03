#!/bin/bash
# 백업 복원 스크립트
# 사용법: chmod +x restore.sh && ./restore.sh

set -e  # 에러 발생 시 중단

BACKUP_DIR="/root/workspace/crowdworks/crowdworks-multiagent-system"
cd $BACKUP_DIR

echo "=== CrowdWorks Multiagent System 백업 복원 ==="
echo ""

# 백업 파일 존재 확인
echo "백업 파일 확인 중..."
MISSING_FILES=0

if [ ! -f "env_backup.tar.gz" ]; then
    echo "❌ env_backup.tar.gz 파일이 없습니다"
    MISSING_FILES=1
fi

if [ ! -f "db_backup.tar.gz" ]; then
    echo "❌ db_backup.tar.gz 파일이 없습니다"
    MISSING_FILES=1
fi

if [ ! -f "elasticsearch_backup.tar.gz" ]; then
    echo "❌ elasticsearch_backup.tar.gz 파일이 없습니다"
    MISSING_FILES=1
fi

if [ ! -f "neo4j_volume_backup.tar.gz" ]; then
    echo "❌ neo4j_volume_backup.tar.gz 파일이 없습니다"
    MISSING_FILES=1
fi

if [ $MISSING_FILES -eq 1 ]; then
    echo ""
    echo "백업 파일을 먼저 이 디렉토리에 업로드하세요:"
    echo "  scp *.tar.gz root@서버IP:$BACKUP_DIR/"
    exit 1
fi

echo "✓ 모든 백업 파일 확인 완료"
echo ""

# 사용자 확인
read -p "복원을 시작하시겠습니까? 기존 데이터가 덮어씌워집니다. (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "복원이 취소되었습니다."
    exit 0
fi

echo ""
echo "=== 복원 시작 ==="
echo ""

# 1. 환경 변수 복원
echo "[1/5] 환경 변수 복원 중..."
tar -xzf env_backup.tar.gz
echo "✓ 환경 변수 복원 완료"
echo ""
echo "⚠️  경고: .env 파일의 다음 항목들을 반드시 새 값으로 교체하세요:"
echo "   - OPENAI_API_KEY"
echo "   - GEMINI_API_KEY_1, GEMINI_API_KEY_2"
echo "   - SERPER_API_KEY"
echo "   - LANGSMITH_API_KEY"
echo "   - POSTGRES_PASSWORD"
echo "   - NEO4J_PASSWORD"
echo "   - ELASTICSEARCH_PASSWORD"
echo ""
read -p "API 키와 비밀번호를 교체하셨습니까? (yes/no): " API_CONFIRM
if [ "$API_CONFIRM" != "yes" ]; then
    echo ""
    echo "복원을 일시 중지합니다."
    echo "nano .env 명령으로 API 키를 교체한 후 다시 실행하세요."
    exit 0
fi

# 2. Docker volumes 생성
echo ""
echo "[2/5] Docker volumes 생성 중..."
docker volume create elasticsearch_es_data 2>/dev/null || echo "  elasticsearch_es_data 이미 존재"
docker volume create neo4j_data_original 2>/dev/null || echo "  neo4j_data_original 이미 존재"
echo "✓ Docker volumes 생성 완료"

# 3. Elasticsearch 데이터 복원
echo ""
echo "[3/5] Elasticsearch 데이터 복원 중... (1.85GB, 시간이 걸릴 수 있습니다)"
docker run --rm -v elasticsearch_es_data:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/elasticsearch_backup.tar.gz"
echo "✓ Elasticsearch 데이터 복원 완료"

# 4. Neo4j volume 데이터 복원
echo ""
echo "[4/5] Neo4j volume 데이터 복원 중..."
docker run --rm -v neo4j_data_original:/data -v $(pwd):/backup alpine sh -c "cd /data && tar xzf /backup/neo4j_volume_backup.tar.gz"
echo "✓ Neo4j volume 데이터 복원 완료"

# 5. 데이터베이스 디렉토리 복원
echo ""
echo "[5/5] PostgreSQL 및 Neo4j 데이터 디렉토리 복원 중..."
tar -xzf db_backup.tar.gz
echo "✓ 데이터베이스 디렉토리 복원 완료"

echo ""
echo "=== ✅ 백업 복원 완료 ==="
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "다음 단계:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. 시스템 시작:"
echo "   docker-compose up -d"
echo ""
echo "2. 로그 확인:"
echo "   docker-compose logs -f"
echo ""
echo "3. 서비스 접속:"
echo "   - Frontend: http://서버IP:3000"
echo "   - Backend API: http://서버IP:8000"
echo "   - Neo4j Browser: http://서버IP:7474"
echo "   - Kibana: http://서버IP:5601"
echo ""
echo "4. 백업 파일 정리 (선택):"
echo "   rm -f *.tar.gz"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
