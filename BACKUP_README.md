━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CrowdWorks Multiagent System - 백업 파일 사용법
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📦 백업 파일 4개:
  1. env_backup.tar.gz (2KB) - API 키, 비밀번호
  2. db_backup.tar.gz (59.1MB) - PostgreSQL + Neo4j 데이터
  3. elasticsearch_backup.tar.gz (1.85GB) - Elasticsearch 데이터
  4. neo4j_volume_backup.tar.gz (92.3MB) - Neo4j volume 데이터

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚀 새 서버에서 복원하는 방법:

1️⃣ Git Clone
   cd /root/workspace/crowdworks/
   git clone <repository-url> crowdworks-multiagent-system
   cd crowdworks-multiagent-system

2️⃣ 백업 파일 업로드 (로컬 PC에서 실행)
   scp env_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/
   scp db_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/
   scp elasticsearch_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/
   scp neo4j_volume_backup.tar.gz root@새서버IP:/root/workspace/crowdworks/crowdworks-multiagent-system/

3️⃣ 자동 복원 스크립트 실행
   chmod +x restore.sh
   ./restore.sh

4️⃣ 시스템 시작
   docker-compose up -d

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️  중요: 보안 조치

새 서버에서는 반드시 다음 항목들을 새 값으로 교체하세요:
  - OPENAI_API_KEY
  - GEMINI_API_KEY_1, GEMINI_API_KEY_2
  - SERPER_API_KEY
  - LANGSMITH_API_KEY
  - POSTGRES_PASSWORD
  - NEO4J_PASSWORD
  - ELASTICSEARCH_PASSWORD

이전 서버는 해킹당했으므로 모든 인증 정보를 교체해야 합니다!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📖 상세 가이드:
   - RESTORE_GUIDE.md 파일 참고
   - restore.sh 스크립트 자동 실행

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💾 백업 파일 보관:
   - 안전한 곳에 백업 파일 4개를 보관하세요
   - 이 README 파일도 함께 보관하세요
   - RESTORE_GUIDE.md와 restore.sh도 함께 보관하세요

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
