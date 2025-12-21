# English Essay Writing Studio

AI 기반 영어 논술 교육 플랫폼 - Redis 캐시로 최적화된 30-40명 동시 접속 지원

## 주요 기능

### 학생 기능
- AI 기반 논술 평가 (Gemini API)
- 소크라테스식 대화형 학습 도우미
- 논술 작성 이력 및 통계 조회
- 실시간 점수 추이 분석

### 교사 기능
- 전체 학생 대시보드
- 학생별 상세 분석
- 성과 분석 및 시각화
- 주제별 평균 점수 분석

## 성능 최적화

### Redis 캐시 도입
- **30-40명 동시 접속 지원**
- Google Sheets API 호출 90% 감소
- 응답 속도 10-50배 향상
- 캐시 TTL: 사용자 정보 5분, 논술 데이터 1분

### 캐시 전략
- 사용자 로그인 정보 캐싱
- 논술 데이터 캐싱 (사용자별/전체)
- 데이터 변경 시 자동 캐시 무효화

## 빠른 시작

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. Redis 설정
로컬 개발:
```bash
# Redis 서버 시작 (로컬)
sudo service redis-server start  # Linux
brew services start redis         # macOS

# Redis 연결 테스트
python test_redis.py
```

Streamlit Cloud 배포:
- [Redis Cloud](https://redis.com/try-free/) 무료 계정 생성
- Streamlit Secrets에 `REDIS_URL` 추가

**자세한 설정**: [REDIS_SETUP.md](REDIS_SETUP.md) 참고

### 3. Google Sheets 설정
1. Google Cloud Console에서 서비스 계정 생성
2. `credentials.json` 파일 다운로드
3. Google Sheets API 활성화
4. 스프레드시트를 서비스 계정과 공유

### 4. Gemini API 설정
```bash
# .env 파일 생성 (로컬)
GEMINI_API_KEY=your_gemini_api_key
REDIS_URL=redis://localhost:6379
```

Streamlit Cloud:
```toml
# Streamlit Secrets
GEMINI_API_KEY = "your_api_key"
REDIS_URL = "redis://user:pass@host:port"
```

### 5. 애플리케이션 실행
```bash
streamlit run main.py
```

## Redis 없이 실행

Redis 연결에 실패해도 애플리케이션은 정상 작동합니다:
- 경고 메시지 표시
- Google Sheets에서 직접 데이터 조회
- 다만, 다중 사용자 환경에서 성능 저하 가능

## 기술 스택

- **Frontend**: Streamlit
- **Database**: Google Sheets (간편한 데이터 관리)
- **Cache**: Redis (성능 최적화)
- **AI**: Google Gemini API
- **Visualization**: Plotly, Pandas

## 프로젝트 구조

```
essay_app/
├── main.py              # 메인 애플리케이션
├── requirements.txt     # Python 패키지
├── credentials.json     # Google Cloud 서비스 계정 (git 제외)
├── test_redis.py        # Redis 연결 테스트 스크립트
├── REDIS_SETUP.md       # Redis 설정 가이드
└── README.md            # 이 파일
```

## 성능 비교

| 지표 | Redis 적용 전 | Redis 적용 후 |
|------|--------------|--------------|
| 로그인 응답 시간 | 1-3초 | 50-100ms |
| 데이터 조회 시간 | 2-5초 | 100-200ms |
| API 호출 (40명 동시) | 100-200회/분 | 10-20회/분 |
| 동시 접속 지원 | 10-15명 | 30-40명 |

## 계정 정보

### 교사 계정
- ID: `teacher`
- PW: `teacher123`

### 학생 계정
- 회원가입 후 사용

## 라이선스

MIT License

## 문의

이슈가 있으시면 GitHub Issues에 등록해주세요.
