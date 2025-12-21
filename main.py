import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import hashlib
import datetime
import os
import google.generativeai as genai
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import redis
import json

# 환경 변수는 Streamlit Cloud에서 secrets로 관리됨

# 페이지 설정
st.set_page_config(
    page_title="English Essay Writing Studio",
    page_icon="✍️",
    layout="wide"
)

# Google Sheets 설정
SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
          'https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = 'credentials.json'
SHEET_ID = '1_HkNcnWX_31GhJwDcT3a2D41BJvbF9Njmwi5d5T8pWQ'

# Gemini API 키 가져오기
def get_gemini_api_key():
    try:
        return st.secrets["GEMINI_API_KEY"]
    except:
        return os.getenv('GEMINI_API_KEY')

# 교사 계정 설정 (고정)
TEACHER_USERNAME = "teacher"
TEACHER_PASSWORD = "teacher123"

# Redis 연결 및 캐시 설정
@st.cache_resource
def get_redis_client():
    """Redis 클라이언트 연결"""
    try:
        # Streamlit Cloud에서 secrets 사용
        try:
            redis_url = st.secrets.get("REDIS_URL", "redis://localhost:6379")
        except:
            # 로컬에서는 환경 변수 또는 기본값 사용
            redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')

        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()  # 연결 테스트
        return client
    except Exception as e:
        st.warning(f"Redis 연결 실패 (캐시 없이 계속): {e}")
        return None

# 캐시 TTL 설정 (초 단위)
CACHE_TTL_USERS = 300  # 사용자 정보: 5분
CACHE_TTL_ESSAYS = 60   # 논술 데이터: 1분 (자주 변경될 수 있음)

def get_from_cache(key):
    """캐시에서 데이터 조회"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            data = redis_client.get(key)
            if data:
                return json.loads(data)
    except Exception as e:
        pass  # 캐시 실패 시 조용히 무시
    return None

def set_to_cache(key, value, ttl=300):
    """캐시에 데이터 저장"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            redis_client.setex(key, ttl, json.dumps(value))
    except Exception as e:
        pass  # 캐시 실패 시 조용히 무시

def invalidate_cache(pattern):
    """캐시 무효화 (패턴 매칭)"""
    try:
        redis_client = get_redis_client()
        if redis_client:
            keys = redis_client.keys(pattern)
            if keys:
                redis_client.delete(*keys)
    except Exception as e:
        pass  # 캐시 실패 시 조용히 무시

@st.cache_resource
def get_google_sheets():
    """Google Sheets 연결"""
    try:
        # Streamlit Cloud에서 secrets 사용
        try:
            credentials = Credentials.from_service_account_info(
                st.secrets["gcp_service_account"], scopes=SCOPES)
        except:
            # 로컬에서는 파일 사용
            credentials = Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        
        gc = gspread.authorize(credentials)
        sheet = gc.open_by_key(SHEET_ID)
        return sheet
    except Exception as e:
        st.error(f"Google Sheets 연결 실패: {e}")
        return None

def hash_password(password):
    """비밀번호 해시화 (SHA-256)"""
    return hashlib.sha256(password.encode()).hexdigest()

def login_teacher(username, password):
    """교사 로그인 검증 - 고정 계정으로 단순화"""
    return str(username).strip() == TEACHER_USERNAME and str(password).strip() == TEACHER_PASSWORD

def register_user(username, password, name):
    """사용자 등록 함수 (Redis 캐시 무효화 포함)"""
    try:
        sheet = get_google_sheets()
        if not sheet:
            return False, "Google Sheets 연결 실패"

        users_sheet = sheet.worksheet('사용자정보')
        existing_users = users_sheet.get_all_records()

        for user in existing_users:
            if str(user['아이디']).strip() == str(username).strip():
                return False, "이미 존재하는 아이디입니다"
        # 평문 암호 저장 (교육용)
        current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        users_sheet.append_row([username, password, name, current_date])

        # 사용자 캐시 무효화
        invalidate_cache("users:*")

        return True, "회원가입이 완료되었습니다!"

    except Exception as e:
        return False, f"등록 중 오류 발생: {str(e)}"

def login_user(username, password):
    """사용자 로그인 함수 (Redis 캐시 적용)"""
    try:
        # 캐시 키 생성
        cache_key = f"users:all"

        # 캐시에서 먼저 조회
        cached_users = get_from_cache(cache_key)

        if cached_users is None:
            # 캐시 미스 - Google Sheets에서 조회
            sheet = get_google_sheets()
            if not sheet:
                return False, "Google Sheets 연결 실패", None

            users_sheet = sheet.worksheet('사용자정보')
            users = users_sheet.get_all_records()

            # 캐시에 저장
            set_to_cache(cache_key, users, CACHE_TTL_USERS)
        else:
            # 캐시 히트
            users = cached_users

        for user in users:
            if str(user['아이디']).strip() == str(username).strip() and str(user['비밀번호']).strip() == str(password).strip():
                return True, "로그인 성공!", user['이름']

        return False, "아이디 또는 비밀번호가 올바르지 않습니다", None

    except Exception as e:
        return False, f"로그인 중 오류 발생: {str(e)}", None

def extract_score_from_feedback(feedback):
    """피드백에서 총점 추출"""
    try:
        lines = feedback.split('\n')
        for line in lines:
            if '총점:' in line:
                score_part = line.split(':')[1].strip()
                score = int(score_part.split('/')[0].strip())
                return score
    except:
        pass
    return 0

def get_ai_feedback(essay_text, topic):
    """Gemini AI를 통한 영어 논술 피드백"""
    try:
        # API 키 설정
        api_key = get_gemini_api_key()
        if not api_key:
            return "Gemini API 키가 설정되지 않았습니다.\n\n총점: 0/100점"

        genai.configure(api_key=api_key)

        # 여러 모델을 순차적으로 시도 (할당량 초과 대비)
        model_names = [
            'gemini-2.5-flash',
            'gemini-2.5-pro',
            'gemini-1.5-flash',
            'gemini-pro'
        ]

        model = None
        last_error = None

        for model_name in model_names:
            try:
                model = genai.GenerativeModel(model_name)
                break
            except Exception as e:
                last_error = str(e)
                continue

        if not model:
            return f"모든 Gemini 모델 사용 실패. 마지막 오류: {last_error}\n\n총점: 0/100점"
        
        prompt = f"""
        한국 학생이 작성한 영어 논술문을 평가해주세요.
        
        주제: {topic}
        논술문: {essay_text}
        
        다음 기준으로 평가하고 피드백은 한국어로 작성해주세요:
        
        1. Content & Ideas (20점) - 주제를 얼마나 잘 다루고 관련된 아이디어를 제시했는가?
        2. Organization & Structure (20점) - 서론, 본론, 결론이 명확하고 잘 구성되었는가?
        3. Language Use & Grammar (20점) - 문법, 어휘, 문장 구조가 적절한가?
        4. Coherence & Cohesion (20점) - 아이디어가 논리적으로 연결되고 전환이 자연스러운가?
        5. Task Achievement (20점) - 주어진 과제 요구사항을 충족했는가?
        
        다음 형식으로 피드백을 작성해주세요:
        
        **평가 결과**
        
        **1. 내용과 아이디어: X/20점**
        - 내용과 아이디어에 대한 상세한 피드백 (한국어)
        
        **2. 구성과 구조: X/20점**
        - 구성에 대한 상세한 피드백 (한국어)
        
        **3. 언어 사용과 문법: X/20점**
        - 언어와 문법에 대한 상세한 피드백 (한국어)
        
        **4. 일관성과 응집성: X/20점**
        - 일관성과 응집성에 대한 상세한 피드백 (한국어)
        
        **5. 과제 달성도: X/20점**
        - 과제 달성도에 대한 상세한 피드백 (한국어)
        
        **총점: X/100점**
        
        **종합 의견:**
        - 논술문의 강점 (한국어)
        - 개선이 필요한 부분 (한국어)
        - 더 나은 글쓰기를 위한 구체적인 제안 (한국어)
        
        **문법 및 표현 수정 제안 (있는 경우):**
        - 원문: [문제가 있는 영어 문장]
        - 수정안: [개선된 영어 문장]
        - 설명: [왜 수정이 필요한지 한국어로 설명]
        
        모든 피드백은 한국어로 작성하되, 학생이 사용한 영어 표현과 수정 권장 표현은 영어 그대로 유지해주세요.
        """
        
        response = model.generate_content(prompt)

        if not response or not response.text:
            return f"AI 응답이 비어있습니다. API 상태를 확인해주세요.\n\n총점: 0/100점"

        return response.text

    except Exception as e:
        error_detail = str(e)
        st.error(f"상세 오류: {error_detail}")
        return f"AI 피드백 생성 중 오류가 발생했습니다: {error_detail}\n\n해결방법:\n1. Gemini API 키가 유효한지 확인\n2. API 할당량이 남아있는지 확인\n3. 인터넷 연결 확인\n\n총점: 0/100점"

def get_chatbot_response(user_message, topic, conversation_history):
    """AI 챗봇 응답 생성 - 소크라테스식 질문 중심 + 제한적 아이디어 제공"""
    try:
        # API 키 설정
        api_key = get_gemini_api_key()
        if not api_key:
            return "Gemini API 키가 설정되지 않았습니다."

        genai.configure(api_key=api_key)

        # 여러 모델을 순차적으로 시도 (할당량 초과 대비)
        model_names = [
            'gemini-2.5-flash',
            'gemini-2.5-pro',
            'gemini-1.5-flash',
            'gemini-pro'
        ]

        model = None

        for model_name in model_names:
            try:
                model = genai.GenerativeModel(model_name)
                break
            except:
                continue

        if not model:
            return "모든 Gemini 모델 사용 실패. API 키를 확인해주세요."
        
        context = ""
        if conversation_history:
            context = "\n이전 대화:\n"
            for msg in conversation_history[-3:]:
                context += f"학생: {msg['user']}\n도우미: {msg['bot']}\n\n"
        
        prompt = f"""
        당신은 영어 논술 학습을 도와주는 소크라테스식 AI 도우미입니다. 
        
        **중요한 제약사항:**
        1. 현재 주제 "{topic}"와 관련된 질문에만 답변하세요
        2. 주제와 무관한 질문(일상 대화, 다른 과목, 개인적 질문 등)에는 "죄송해요, 현재 설정된 논술 주제에 집중해서 대화해요. 다른 질문이 있으시면 주제를 바꿔주세요!"라고 답변하세요
        
        **응답 방식:**
        - 주로 학생이 스스로 생각할 수 있는 질문을 던지세요 (70%)
        - 필요시 아주 간단한 힌트나 예시를 1-2개만 제공하세요 (30%)
        - 절대 완전한 답이나 긴 설명을 주지 마세요
        - 1-2문장으로 간결하게 응답하세요

        현재 논술 주제: {topic}
        
        {context}
        
        학생의 질문/메시지: {user_message}
        
        **좋은 응답 예시:**
        학생: "소셜미디어 장점을 모르겠어요"
        → "당신이 매일 사용하는 소셜미디어에서 가장 유용하다고 느끼는 순간은 언제인가요?"
        
        학생: "환경보호 예시가 필요해요"  
        → "플라스틱 사용을 생각해보세요. 일주일 동안 당신이 버리는 플라스틱을 세어본다면 어떨까요?"
        
        학생: "오늘 날씨가 어때요?" (주제 무관)
        → "죄송해요, 현재 설정된 논술 주제에 집중해서 대화해요. 다른 질문이 있으시면 주제를 바꿔주세요!"
        
        학생이 스스로 답을 찾아갈 수 있도록 이끄는 질문과 최소한의 힌트만 제공하세요.
        """
        
        response = model.generate_content(prompt)

        if not response or not response.text:
            return "AI 응답이 비어있습니다. 잠시 후 다시 시도해주세요."

        return response.text

    except Exception as e:
        return f"챗봇 응답 생성 중 오류가 발생했습니다: {str(e)}\n\nGemini API 상태를 확인해주세요."

def save_essay_to_sheet(username, user_name, topic, essay_content, score, feedback):
    """논술문을 구글시트에 저장 (Redis 캐시 무효화 포함)"""
    try:
        sheet = get_google_sheets()
        if not sheet:
            return False, "Google Sheets 연결 실패"

        try:
            essay_sheet = sheet.worksheet('논술데이터')
        except gspread.WorksheetNotFound:
            essay_sheet = sheet.add_worksheet(title='논술데이터', rows=1000, cols=7)
            essay_sheet.append_row(['아이디', '이름', '날짜', '주제', '논술문', '점수', '피드백'])

        current_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row_data = [username, user_name, current_date, topic, essay_content, str(score), feedback]
        essay_sheet.append_row(row_data)

        # 논술 데이터 캐시 무효화 (해당 사용자 및 전체 데이터)
        invalidate_cache(f"essays:user:{username}")
        invalidate_cache("essays:all")

        return True, "논술문이 성공적으로 저장되었습니다!"

    except Exception as e:
        return False, f"저장 중 오류 발생: {str(e)}"

def get_user_essays(username):
    """사용자의 논술 작성 이력 가져오기 (Redis 캐시 적용)"""
    try:
        # 캐시 키 생성
        cache_key = f"essays:user:{username}"

        # 캐시에서 먼저 조회
        cached_essays = get_from_cache(cache_key)

        if cached_essays is None:
            # 캐시 미스 - Google Sheets에서 조회
            sheet = get_google_sheets()
            if not sheet:
                return None, "Google Sheets 연결 실패"

            essay_sheet = sheet.worksheet('논술데이터')
            all_data = essay_sheet.get_all_records()
            user_essays = [row for row in all_data if row['아이디'] == username]
            user_essays.sort(key=lambda x: x['날짜'], reverse=True)

            # 캐시에 저장
            set_to_cache(cache_key, user_essays, CACHE_TTL_ESSAYS)
        else:
            # 캐시 히트
            user_essays = cached_essays

        return user_essays, None

    except Exception as e:
        return None, f"데이터 조회 중 오류: {str(e)}"

def get_all_essays():
    """모든 학생의 논술 데이터 가져오기 (교사용, Redis 캐시 적용)"""
    try:
        # 캐시 키 생성
        cache_key = "essays:all"

        # 캐시에서 먼저 조회
        cached_essays = get_from_cache(cache_key)

        if cached_essays is None:
            # 캐시 미스 - Google Sheets에서 조회
            sheet = get_google_sheets()
            if not sheet:
                return None, "Google Sheets 연결 실패"

            essay_sheet = sheet.worksheet('논술데이터')
            all_data = essay_sheet.get_all_records()
            all_data.sort(key=lambda x: x['날짜'], reverse=True)

            # 캐시에 저장
            set_to_cache(cache_key, all_data, CACHE_TTL_ESSAYS)
        else:
            # 캐시 히트
            all_data = cached_essays

        return all_data, None

    except Exception as e:
        return None, f"데이터 조회 중 오류: {str(e)}"

def calculate_user_stats(essays):
    """사용자 통계 계산"""
    if not essays:
        return {
            'total_essays': 0,
            'average_score': 0,
            'best_score': 0,
            'latest_score': 0,
            'improvement': 0
        }
    
    scores = [int(essay['점수']) for essay in essays if essay['점수']]
    
    stats = {
        'total_essays': len(essays),
        'average_score': round(sum(scores) / len(scores), 1) if scores else 0,
        'best_score': max(scores) if scores else 0,
        'latest_score': scores[0] if scores else 0,
    }
    
    if len(scores) >= 6:
        recent_avg = sum(scores[:3]) / 3
        old_avg = sum(scores[-3:]) / 3
        stats['improvement'] = round(recent_avg - old_avg, 1)
    else:
        stats['improvement'] = 0
    
    return stats

def calculate_class_stats(all_essays):
    """전체 반 통계 계산"""
    if not all_essays:
        return {
            'total_students': 0,
            'total_essays': 0,
            'class_average': 0,
            'active_students': 0
        }
    
    student_data = {}
    for essay in all_essays:
        student_id = essay['아이디']
        if student_id not in student_data:
            student_data[student_id] = []
        student_data[student_id].append(int(essay['점수']) if essay['점수'] else 0)
    
    all_scores = [int(essay['점수']) for essay in all_essays if essay['점수']]
    
    recent_date = datetime.datetime.now() - datetime.timedelta(days=30)
    active_students = len(set([
        essay['아이디'] for essay in all_essays 
        if datetime.datetime.strptime(essay['날짜'][:10], "%Y-%m-%d") > recent_date
    ]))
    
    return {
        'total_students': len(student_data),
        'total_essays': len(all_essays),
        'class_average': round(sum(all_scores) / len(all_scores), 1) if all_scores else 0,
        'active_students': active_students
    }

def render_teacher_dashboard():
    """교사 대시보드 렌더링"""
    st.header("👨‍🏫 교사 대시보드")
    
    with st.spinner("📊 전체 데이터를 불러오는 중..."):
        all_essays, error = get_all_essays()
    
    if error:
        st.error(f"❌ 데이터 로드 실패: {error}")
        return
    
    if not all_essays:
        st.info("📝 아직 제출된 논술문이 없습니다.")
        return
    
    class_stats = calculate_class_stats(all_essays)
    
    st.markdown("### 📈 전체 반 현황")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("👥 전체 학생 수", f"{class_stats['total_students']}명")
    with col2:
        st.metric("📝 총 논술 수", f"{class_stats['total_essays']}편")
    with col3:
        st.metric("📊 반 평균 점수", f"{class_stats['class_average']}점")
    with col4:
        st.metric("🔥 활동 학생 수", f"{class_stats['active_students']}명", 
                 help="최근 30일 내 논술 제출 학생")
    
    st.markdown("---")
    
    tab1, tab2, tab3, tab4 = st.tabs(["📊 성과 분석", "👥 학생별 현황", "📋 최근 제출물", "📈 추이 분석"])
    
    with tab1:
        st.markdown("### 📊 성과 분석")
        
        scores = [int(essay['점수']) for essay in all_essays if essay['점수']]
        
        col_chart1, col_chart2 = st.columns(2)
        
        with col_chart1:
            st.markdown("**점수 분포**")
            fig_hist = px.histogram(
                x=scores, 
                nbins=20, 
                title="점수 분포 히스토그램",
                labels={'x': '점수', 'y': '빈도'}
            )
            fig_hist.update_layout(height=300)
            st.plotly_chart(fig_hist, use_container_width=True)
        
        with col_chart2:
            st.markdown("**등급별 분포**")
            grade_counts = {'A (90-100)': 0, 'B (80-89)': 0, 'C (70-79)': 0, 'D (60-69)': 0, 'F (0-59)': 0}
            for score in scores:
                if score >= 90: grade_counts['A (90-100)'] += 1
                elif score >= 80: grade_counts['B (80-89)'] += 1
                elif score >= 70: grade_counts['C (70-79)'] += 1
                elif score >= 60: grade_counts['D (60-69)'] += 1
                else: grade_counts['F (0-59)'] += 1
            
            fig_pie = px.pie(
                values=list(grade_counts.values()),
                names=list(grade_counts.keys()),
                title="등급별 분포"
            )
            fig_pie.update_layout(height=300)
            st.plotly_chart(fig_pie, use_container_width=True)
        
        st.markdown("**주제별 평균 점수**")
        topic_scores = {}
        for essay in all_essays:
            topic = essay['주제'][:40] + "..." if len(essay['주제']) > 40 else essay['주제']
            score = int(essay['점수']) if essay['점수'] else 0
            if topic not in topic_scores:
                topic_scores[topic] = []
            topic_scores[topic].append(score)
        
        topic_averages = {topic: sum(scores)/len(scores) for topic, scores in topic_scores.items()}
        topic_df = pd.DataFrame(list(topic_averages.items()), columns=['주제', '평균점수'])
        topic_df = topic_df.sort_values('평균점수', ascending=True)
        
        fig_bar = px.bar(
            topic_df, 
            x='평균점수', 
            y='주제', 
            orientation='h',
            title="주제별 평균 점수"
        )
        fig_bar.update_layout(height=400)
        st.plotly_chart(fig_bar, use_container_width=True)
    
    with tab2:
        st.markdown("### 👥 학생별 현황")
        
        student_stats = {}
        for essay in all_essays:
            student_id = essay['아이디']
            student_name = essay['이름']
            
            if student_id not in student_stats:
                student_stats[student_id] = {
                    'name': student_name,
                    'essays': [],
                    'total_count': 0,
                    'average_score': 0,
                    'best_score': 0,
                    'latest_date': ''
                }
            
            score = int(essay['점수']) if essay['점수'] else 0
            student_stats[student_id]['essays'].append(score)
            student_stats[student_id]['total_count'] += 1
            student_stats[student_id]['latest_date'] = max(
                student_stats[student_id]['latest_date'], 
                essay['날짜']
            )
        
        for student_id, stats in student_stats.items():
            scores = stats['essays']
            stats['average_score'] = round(sum(scores) / len(scores), 1) if scores else 0
            stats['best_score'] = max(scores) if scores else 0
        
        st.markdown("**학생별 성과 요약**")
        
        sort_option = st.selectbox(
            "정렬 기준", 
            ["평균 점수 높은순", "평균 점수 낮은순", "제출 횟수 많은순", "최근 활동순"]
        )
        
        student_list = []
        for student_id, stats in student_stats.items():
            student_list.append({
                '아이디': student_id,
                '이름': stats['name'],
                '제출 횟수': stats['total_count'],
                '평균 점수': stats['average_score'],
                '최고 점수': stats['best_score'],
                '최근 제출일': stats['latest_date'][:10]
            })
        
        df = pd.DataFrame(student_list)
        
        if sort_option == "평균 점수 높은순":
            df = df.sort_values('평균 점수', ascending=False)
        elif sort_option == "평균 점수 낮은순":
            df = df.sort_values('평균 점수', ascending=True)
        elif sort_option == "제출 횟수 많은순":
            df = df.sort_values('제출 횟수', ascending=False)
        elif sort_option == "최근 활동순":
            df = df.sort_values('최근 제출일', ascending=False)
        
        st.dataframe(df, use_container_width=True)
        
        st.markdown("**개별 학생 상세 분석**")
        selected_student = st.selectbox(
            "학생 선택:", 
            [f"{row['이름']} ({row['아이디']})" for _, row in df.iterrows()]
        )
        
        if selected_student:
            student_id = selected_student.split('(')[1].split(')')[0]
            student_essays = [essay for essay in all_essays if essay['아이디'] == student_id]
            
            # 회차별로 변경 (최신 데이터부터 역순)
            scores = [int(essay['점수']) if essay['점수'] else 0 for essay in student_essays]
            
            if len(scores) > 1:
                # 1회차부터 시작하도록 데이터 정렬
                scores_display = list(reversed(scores))
                essay_numbers = list(range(1, len(scores_display) + 1))
                
                chart_data = {
                    '회차': essay_numbers,
                    '점수': scores_display
                }
                
                st.line_chart(data=chart_data, x='회차', y='점수', height=300)
            else:
                st.info("점수 추이를 보려면 최소 2개 이상의 제출물이 필요합니다.")
    
    with tab3:
        st.markdown("### 📋 최근 제출물")
        
        recent_count = st.slider("표시할 최근 제출물 수", 5, 50, 20)
        recent_essays = all_essays[:recent_count]
        
        for i, essay in enumerate(recent_essays):
            with st.expander(f"📄 {essay['날짜']} - {essay['이름']} ({essay['아이디']}) - {essay['점수']}점"):
                col_detail1, col_detail2 = st.columns([3, 1])
                
                with col_detail1:
                    st.markdown(f"**📝 주제:** {essay['주제']}")
                    st.markdown(f"**✍️ 논술문:**")
                    st.text_area("논술문 내용", value=essay['논술문'], height=150, disabled=True, key=f"teacher_essay_{i}", label_visibility="collapsed")
                
                with col_detail2:
                    score = int(essay['점수']) if essay['점수'] else 0
                    st.markdown(f"**📊 점수:** {score}/100점")
                    
                    if score >= 90:
                        st.success("🏆 Excellent")
                    elif score >= 80:
                        st.info("😊 Good")
                    elif score >= 70:
                        st.warning("📚 Average")
                    else:
                        st.error("💪 Needs Improvement")
                
                if st.button(f"📋 AI 피드백 보기", key=f"teacher_feedback_{i}"):
                    st.markdown("**🤖 AI 피드백:**")
                    st.markdown(essay['피드백'])
    
    with tab4:
        st.markdown("### 📈 추이 분석")
        
        if len(all_essays) >= 10:
            monthly_data = {}
            for essay in all_essays:
                month_key = essay['날짜'][:7]
                score = int(essay['점수']) if essay['점수'] else 0
                
                if month_key not in monthly_data:
                    monthly_data[month_key] = []
                monthly_data[month_key].append(score)
            
            monthly_averages = {
                month: sum(scores) / len(scores) 
                for month, scores in monthly_data.items() 
                if len(scores) >= 3
            }
            
            if monthly_averages:
                months = sorted(monthly_averages.keys())
                averages = [monthly_averages[month] for month in months]
                
                fig_trend = px.line(
                    x=months, 
                    y=averages,
                    title="월별 반 평균 점수 추이",
                    labels={'x': '월', 'y': '평균 점수'}
                )
                fig_trend.update_layout(height=400)
                st.plotly_chart(fig_trend, use_container_width=True)
            
            weekly_submissions = {}
            for essay in all_essays:
                date_obj = datetime.datetime.strptime(essay['날짜'][:10], "%Y-%m-%d")
                week_key = date_obj.strftime("%Y-W%U")
                
                if week_key not in weekly_submissions:
                    weekly_submissions[week_key] = 0
                weekly_submissions[week_key] += 1
            
            recent_weeks = sorted(weekly_submissions.keys())[-10:]
            submission_counts = [weekly_submissions[week] for week in recent_weeks]
            
            fig_submissions = px.bar(
                x=recent_weeks,
                y=submission_counts,
                title="주간 논술 제출량 추이",
                labels={'x': '주차', 'y': '제출 수'}
            )
            fig_submissions.update_layout(height=400)
            st.plotly_chart(fig_submissions, use_container_width=True)
        
        else:
            st.info("📈 추이 분석을 위해서는 더 많은 데이터가 필요합니다 (최소 10개 이상).")

def main():
    # 세션 상태 초기화
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'username' not in st.session_state:
        st.session_state.username = ""
    if 'user_name' not in st.session_state:
        st.session_state.user_name = ""
    if 'is_teacher' not in st.session_state:
        st.session_state.is_teacher = False

    st.title("✍️ English Essay Writing Studio")
    st.markdown("---")
    
    if not st.session_state.logged_in:
        st.markdown("### 🔐 로그인이 필요합니다")
        
        tab1, tab2, tab3 = st.tabs(["🔑 학생 로그인", "👨‍🏫 교사 로그인", "📝 회원가입"])
        
        with tab1:
            st.subheader("학생 로그인")
            
            with st.form("student_login_form"):
                username = st.text_input("아이디", placeholder="아이디를 입력하세요")
                password = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
                
                submitted = st.form_submit_button("🔑 학생 로그인", use_container_width=True)
                
                if submitted:
                    if username and password:
                        with st.spinner("로그인 중..."):
                            success, message, user_name = login_user(username, password)
                            
                        if success:
                            st.session_state.logged_in = True
                            st.session_state.username = username
                            st.session_state.user_name = user_name
                            st.session_state.is_teacher = False
                            st.success(f"환영합니다, {user_name}님!")
                            st.rerun()
                        else:
                            st.error(message)
                    else:
                        st.error("아이디와 비밀번호를 모두 입력해주세요")
        
        with tab2:
            st.subheader("교사 로그인")
            st.info("📌 교사 계정: ID: teacher / PW: teacher123")
            
            with st.form("teacher_login_form"):
                teacher_username = st.text_input("교사 아이디", placeholder="teacher")
                teacher_password = st.text_input("교사 비밀번호", type="password", placeholder="teacher123")
                
                submitted = st.form_submit_button("👨‍🏫 교사 로그인", use_container_width=True)
                
                if submitted:
                    if teacher_username and teacher_password:
                        if login_teacher(teacher_username, teacher_password):
                            st.session_state.logged_in = True
                            st.session_state.username = teacher_username
                            st.session_state.user_name = "선생님"
                            st.session_state.is_teacher = True
                            st.success("환영합니다, 선생님!")
                            st.rerun()
                        else:
                            st.error("교사 아이디 또는 비밀번호가 올바르지 않습니다")
                    else:
                        st.error("아이디와 비밀번호를 모두 입력해주세요")
        
        with tab3:
            st.subheader("학생 회원가입")
            
            with st.form("register_form"):
                col1, col2 = st.columns(2)
                
                with col1:
                    new_username = st.text_input("아이디", key="reg_username", placeholder="영문/숫자 조합")
                    new_password = st.text_input("비밀번호", type="password", key="reg_password", placeholder="최소 4자 이상")
                    confirm_password = st.text_input("비밀번호 확인", type="password", placeholder="비밀번호 재입력")
                
                with col2:
                    name = st.text_input("이름", placeholder="실명을 입력하세요")
                
                submitted = st.form_submit_button("📝 회원가입", use_container_width=True)
                
                if submitted:
                    if not all([new_username, new_password, confirm_password, name]):
                        st.error("모든 필드를 입력해주세요")
                    elif len(new_password) < 4:
                        st.error("비밀번호는 최소 4자 이상이어야 합니다")
                    elif new_password != confirm_password:
                        st.error("비밀번호가 일치하지 않습니다")
                    elif len(new_username) < 3:
                        st.error("아이디는 최소 3자 이상이어야 합니다")
                    else:
                        with st.spinner("회원가입 처리 중..."):
                            success, message = register_user(new_username, new_password, name)
                        
                        if success:
                            st.success(message)
                            st.info("이제 학생 로그인 탭에서 로그인하세요!")
                        else:
                            st.error(message)
    
    else:
        if st.session_state.is_teacher:
            st.sidebar.markdown("---")
            st.sidebar.markdown(f"### 👨‍🏫 {st.session_state.user_name}")
            st.sidebar.markdown("---")
            
            if st.sidebar.button("🚪 로그아웃", type="primary"):
                st.session_state.logged_in = False
                st.session_state.username = ""
                st.session_state.user_name = ""
                st.session_state.is_teacher = False
                st.success("로그아웃되었습니다!")
                st.rerun()
            
            render_teacher_dashboard()
            
        else:
            st.sidebar.markdown("---")
            st.sidebar.markdown(f"### 👋 {st.session_state.user_name}님")
            st.sidebar.markdown("---")
            
            menu_options = ["📝 논술 작성", "🤖 AI 학습 도우미", "📚 작성 이력", "🚪 로그아웃"]
            selected_menu = st.sidebar.selectbox("📋 메뉴 선택", menu_options)
            
            if selected_menu == "🚪 로그아웃":
                if st.sidebar.button("로그아웃 확인", type="primary"):
                    st.session_state.logged_in = False
                    st.session_state.username = ""
                    st.session_state.user_name = ""
                    st.session_state.is_teacher = False
                    st.success("로그아웃되었습니다!")
                    st.rerun()
            
            if selected_menu == "📝 논술 작성":
                st.header("📝 논술 작성하기")
                
                st.markdown("#### 1️⃣ 논술 주제 설정")
                
                topic_method = st.radio(
                    "주제 설정 방식을 선택하세요:",
                    ["직접 입력", "예시 주제 중 선택"],
                    horizontal=True
                )
                
                if topic_method == "직접 입력":
                    custom_topic = st.text_area(
                        "논술 주제를 입력하세요:",
                        height=100,
                        placeholder="예: Write about your dream vacation plan and explain why you want to go there."
                    )
                    selected_topic = custom_topic
                else:
                    # 중학생 수준에 맞는 주제들로 변경
                    sample_topics = [
                        "Write a text about your plans for the class trip.",
                        "Describe the rules you think are important for a classroom and explain why.",
                        "Write a detective story about solving a mystery with the clues you find.",
                        "Plan an activity for Earth Day and explain how it helps protect the environment.",
                        "Write about three things you will do to protect the environment in your daily life.",
                        "Describe your future career plans and explain why you chose this path.",
                        "Write about a person you admire and explain why they are your role model.",
                        "Explain how to use smartphones wisely and responsibly.",
                        "Compare online shopping and traditional shopping. Which do you prefer and why?",
                        "Write about how to use social media in a positive and safe way.",
                        "Write a book review about your favorite book and recommend it to your friends."
                    ]
                    
                    selected_topic = st.selectbox(
                        "예시 주제 중 선택하세요:",
                        sample_topics
                    )
                
                if selected_topic:
                    st.success(f"**선택된 주제:** {selected_topic}")
                    
                    st.markdown("#### 2️⃣ 논술문 작성")
                    
                    essay_content = st.text_area(
                        "논술문을 작성하세요:",
                        height=400,
                        placeholder="Write your essay here in English...",
                        help="영어로 논술문을 작성해주세요. 최소 100단어 이상 권장합니다."
                    )
                    
                    if essay_content:
                        char_count = len(essay_content)
                        word_count = len(essay_content.split()) if essay_content.strip() else 0
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            st.info(f"📝 단어 수: **{word_count}**")
                        with col2:
                            st.info(f"🔤 글자 수: **{char_count}**")
                    
                    if st.button("📝 제출하기", type="primary", use_container_width=True):
                        word_count = len(essay_content.split()) if essay_content else 0
                        if not essay_content or word_count < 10:
                            st.error("최소 10단어 이상의 논술문을 작성해주세요.")
                        else:
                            with st.spinner("🤖 AI가 논술문을 분석하고 있습니다... (30초 정도 소요)"):
                                feedback = get_ai_feedback(essay_content, selected_topic)
                                score = extract_score_from_feedback(feedback)

                                if "오류가 발생했습니다" not in feedback and score > 0:
                                    # 자동 저장
                                    with st.spinner("💾 결과를 저장하는 중..."):
                                        success, message = save_essay_to_sheet(
                                            st.session_state.username,
                                            st.session_state.user_name,
                                            selected_topic,
                                            essay_content,
                                            score,
                                            feedback
                                        )

                                    if success:
                                        st.success("✅ AI 평가가 완료되고 결과가 저장되었습니다!")
                                        st.balloons()

                                        col_score1, col_score2, col_score3 = st.columns(3)
                                        with col_score2:
                                            st.metric("📊 총점", f"{score}/100점", delta=None)

                                        st.markdown("### 📋 AI 피드백 결과")
                                        st.markdown(feedback)
                                    else:
                                        st.error(f"AI 평가는 완료되었으나 저장 중 오류 발생: {message}")
                                        st.markdown("### 📋 AI 피드백 결과")
                                        st.markdown(feedback)
                                else:
                                    st.error("AI 평가 중 오류가 발생했거나 점수를 추출할 수 없습니다.")
                    
                    with st.expander("📋 영어 논술 작성 가이드"):
                        st.markdown("""
                        ### 📝 영어 논술문 구조
                        
                        **1. Introduction (서론)**
                        - Hook: 흥미로운 질문이나 사실로 시작
                        - Background: 주제에 대한 배경 설명
                        - Thesis Statement: 명확한 주장 제시
                        
                        **2. Body Paragraphs (본론)**
                        - Topic Sentence: 각 단락의 주제 문장
                        - Supporting Details: 구체적인 예시와 근거
                        - Transitions: 단락 간 자연스러운 연결
                        
                        **3. Conclusion (결론)**
                        - Restate Thesis: 주장 재확인
                        - Summary: 주요 포인트 요약
                        - Final Thought: 마무리 생각
                        
                        ### ✅ 작성 팁
                        - 명확하고 간결한 문장 사용
                        - 다양한 어휘와 문법 구조 활용
                        - 논리적 순서로 아이디어 전개
                        - 구체적인 예시로 주장 뒷받침
                        """)
                else:
                    st.warning("먼저 논술 주제를 설정해주세요.")
            
            elif selected_menu == "🤖 AI 학습 도우미":
                st.header("🤖 AI 학습 도우미")
                st.markdown("논술 주제에 대해 자유롭게 대화하며 아이디어를 확장해보세요!")
                
                if 'chatbot_topic' not in st.session_state:
                    st.session_state.chatbot_topic = ""
                if 'chatbot_history' not in st.session_state:
                    st.session_state.chatbot_history = []
                
                st.markdown("#### 1️⃣ 대화할 주제 설정")
                
                topic_input_method = st.radio(
                    "주제 입력 방식:",
                    ["직접 입력", "예시 주제 선택"],
                    horizontal=True,
                    key="chatbot_topic_method"
                )
                
                if topic_input_method == "직접 입력":
                    chatbot_topic = st.text_input(
                        "대화하고 싶은 주제를 입력하세요:",
                        value=st.session_state.chatbot_topic,
                        placeholder="예: 꿈의 여행 계획에 대해 이야기해보고 싶어요"
                    )
                else:
                    sample_topics_korean = [
                        "꿈의 여행 계획 세우기",
                        "학급 규칙 정하기",
                        "추리 소설 쓰기",
                        "환경의 날 활동 기획하기",
                        "환경 보호 실천 방법",
                        "진로 계획 세우기",
                        "롤모델 소개하기",
                        "스마트폰의 현명한 사용법",
                        "온라인 쇼핑과 오프라인 쇼핑 비교",
                        "올바른 SNS 사용법",
                        "좋아하는 책 추천하기"
                    ]
                    
                    chatbot_topic = st.selectbox(
                        "대화할 주제를 선택하세요:",
                        sample_topics_korean,
                        key="chatbot_topic_select"
                    )
                
                if chatbot_topic:
                    if chatbot_topic != st.session_state.chatbot_topic:
                        st.session_state.chatbot_topic = chatbot_topic
                        st.session_state.chatbot_history = []
                        st.success(f"✅ 새로운 주제로 설정되었습니다: **{chatbot_topic}**")
                        st.info("💡 아래에서 이 주제에 대해 자유롭게 질문하거나 의견을 나눠보세요!")
                    
                    st.markdown("#### 2️⃣ AI 도우미와 대화하기")
                    
                    if st.session_state.chatbot_history:
                        st.markdown("**💬 이전 대화:**")
                        for i, msg in enumerate(st.session_state.chatbot_history):
                            with st.expander(f"대화 {i+1}: {msg['user'][:30]}..."):
                                st.markdown(f"**👤 학생:** {msg['user']}")
                                st.markdown(f"**🤖 AI 도우미:** {msg['bot']}")
                        st.markdown("---")
                    
                    with st.form("chatbot_form", clear_on_submit=True):
                        user_message = st.text_area(
                            "메시지를 입력하세요:",
                            height=100,
                            placeholder="예: 이 주제에 대해 어떻게 생각해야 할지 모르겠어요. 어떤 관점에서 접근하면 좋을까요?"
                        )
                        
                        col1, col2, col3 = st.columns([1, 1, 1])
                        with col2:
                            submit_chat = st.form_submit_button("💬 대화하기", use_container_width=True)
                    
                    if submit_chat and user_message:
                        with st.spinner("🤖 AI 도우미가 답변을 준비하고 있습니다..."):
                            bot_response = get_chatbot_response(
                                user_message, 
                                chatbot_topic, 
                                st.session_state.chatbot_history
                            )
                            
                            st.session_state.chatbot_history.append({
                                'user': user_message,
                                'bot': bot_response
                            })
                        
                        # 새로운 대화를 바로 표시
                        st.success("✅ 새로운 대화가 추가되었습니다!")
                        st.markdown("### 📝 방금 나눈 대화")
                        st.markdown(f"**👤 학생:** {user_message}")
                        st.markdown(f"**🤖 AI 도우미:** {bot_response}")
                        st.markdown("---")
                    
                    st.markdown("#### 💡 빠른 질문")
                    quick_questions = [
                        "이 주제에 대해 어떤 관점에서 접근하면 좋을까요?",
                        "반대 의견은 어떤 것들이 있을까요?",
                        "구체적인 예시를 들어주세요",
                        "이 주제로 어떤 구조로 글을 쓰면 좋을까요?"
                    ]
                    
                    cols = st.columns(2)
                    for i, question in enumerate(quick_questions):
                        with cols[i % 2]:
                            if st.button(f"💭 {question}", key=f"quick_{i}"):
                                with st.spinner("🤖 AI 도우미가 답변을 준비하고 있습니다..."):
                                    bot_response = get_chatbot_response(
                                        question, 
                                        chatbot_topic, 
                                        st.session_state.chatbot_history
                                    )
                                    
                                    st.session_state.chatbot_history.append({
                                        'user': question,
                                        'bot': bot_response
                                    })
                                    
                                    st.rerun()
                    
                    if st.session_state.chatbot_history:
                        st.markdown("---")
                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("🗑️ 대화 기록 삭제", type="secondary"):
                                st.session_state.chatbot_history = []
                                st.success("대화 기록이 삭제되었습니다!")
                                st.rerun()
                        
                        with col2:
                            if st.button("📝 논술 작성하러 가기", type="primary"):
                                st.info("논술 작성 메뉴에서 계속 작성해보세요!")
                else:
                    st.warning("먼저 대화할 주제를 설정해주세요.")
            
            elif selected_menu == "📚 작성 이력":
                st.header("📚 나의 작성 이력")
                
                with st.spinner("📊 데이터를 불러오는 중..."):
                    user_essays, error = get_user_essays(st.session_state.username)
                
                if error:
                    st.error(f"❌ {error}")
                    return
                
                if not user_essays:
                    st.info("📝 아직 작성한 논술문이 없습니다. 첫 번째 논술문을 작성해보세요!")
                    if st.button("📝 논술 작성하러 가기", type="primary"):
                        st.rerun()
                    return
                
                stats = calculate_user_stats(user_essays)
                
                st.markdown("### 📈 나의 논술 성과")
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric(
                        label="📝 총 작성 수", 
                        value=f"{stats['total_essays']}편"
                    )
                
                with col2:
                    st.metric(
                        label="📊 평균 점수", 
                        value=f"{stats['average_score']}점"
                    )
                
                with col3:
                    st.metric(
                        label="🏆 최고 점수", 
                        value=f"{stats['best_score']}점"
                    )
                
                with col4:
                    improvement_delta = f"+{stats['improvement']}" if stats['improvement'] > 0 else str(stats['improvement']) if stats['improvement'] < 0 else "0"
                    st.metric(
                        label="📈 최근 향상도", 
                        value=f"{stats['latest_score']}점",
                        delta=improvement_delta if stats['improvement'] != 0 else None
                    )
                
                st.markdown("---")
                
                detail_tab1, detail_tab2, detail_tab3 = st.tabs(["📋 작성 목록", "📊 점수 추이", "🎯 주제별 분석"])
                
                with detail_tab1:
                    st.markdown("### 📋 논술 작성 목록")
                    
                    search_col1, search_col2 = st.columns([2, 1])
                    with search_col1:
                        search_keyword = st.text_input("🔍 주제 검색", placeholder="검색할 키워드를 입력하세요")
                    with search_col2:
                        sort_option = st.selectbox("정렬 기준", ["최신순", "점수 높은순", "점수 낮은순"])
                    
                    filtered_essays = user_essays
                    if search_keyword:
                        filtered_essays = [essay for essay in user_essays if search_keyword.lower() in essay['주제'].lower()]
                    
                    if sort_option == "점수 높은순":
                        filtered_essays.sort(key=lambda x: int(x['점수']) if x['점수'] else 0, reverse=True)
                    elif sort_option == "점수 낮은순":
                        filtered_essays.sort(key=lambda x: int(x['점수']) if x['점수'] else 0)
                    
                    for i, essay in enumerate(filtered_essays[:10]):
                        with st.expander(f"📄 {essay['날짜']} - {essay['주제'][:50]}{'...' if len(essay['주제']) > 50 else ''} (점수: {essay['점수']}점)"):
                            col_detail1, col_detail2 = st.columns([2, 1])
                            
                            with col_detail1:
                                st.markdown(f"**📅 작성일:** {essay['날짜']}")
                                st.markdown(f"**📝 주제:** {essay['주제']}")
                                st.markdown(f"**✍️ 논술문:**")
                                st.text_area("논술문 내용", value=essay['논술문'], height=150, disabled=True, key=f"essay_{i}", label_visibility="collapsed")
                            
                            with col_detail2:
                                st.markdown(f"**📊 점수:** {essay['점수']}/100점")
                                
                                score = int(essay['점수']) if essay['점수'] else 0
                                if score >= 90:
                                    st.success("🏆 Excellent")
                                elif score >= 80:
                                    st.info("😊 Good")
                                elif score >= 70:
                                    st.warning("📚 Average")
                                else:
                                    st.error("💪 Needs Improvement")
                            
                            if st.button(f"📋 피드백 보기", key=f"feedback_{i}"):
                                st.markdown("**🤖 AI 피드백:**")
                                st.markdown(essay['피드백'])
                    
                    if len(filtered_essays) > 10:
                        st.info(f"📌 총 {len(filtered_essays)}개 중 10개만 표시됩니다.")
                
                with detail_tab2:
                    st.markdown("### 📊 점수 변화 추이 (회차별)")
                    
                    if len(user_essays) >= 2:
                        # 회차별로 변경 (최신 데이터부터 역순으로 정렬된 상태에서 회차 번호 부여)
                        scores = [int(essay['점수']) if essay['점수'] else 0 for essay in user_essays]
                        
                        # 그래프를 위해 데이터를 뒤집어서 회차 순서대로 표시 (1회차부터 시작)
                        scores_display = list(reversed(scores))
                        essay_numbers_display = list(range(1, len(scores_display) + 1))
                        
                        chart_data = {
                            '회차': essay_numbers_display,
                            '점수': scores_display
                        }
                        
                        st.line_chart(data=chart_data, x='회차', y='점수', height=300)
                        
                        if len(scores_display) >= 3:
                            trend = "상승" if scores_display[-1] > scores_display[0] else "하락" if scores_display[-1] < scores_display[0] else "유지"
                            st.info(f"📈 **전체적인 추세:** {trend} (1회차: {scores_display[0]}점 → 최근 회차: {scores_display[-1]}점)")
                    
                    else:
                        st.info("📊 점수 추이를 보려면 최소 2개 이상의 논술문이 필요합니다.")
                
                with detail_tab3:
                    st.markdown("### 🎯 주제별 성과 분석")
                    
                    if user_essays:
                        topic_scores = {}
                        for essay in user_essays:
                            topic = essay['주제'][:30] + "..." if len(essay['주제']) > 30 else essay['주제']
                            score = int(essay['점수']) if essay['점수'] else 0
                            
                            if topic not in topic_scores:
                                topic_scores[topic] = []
                            topic_scores[topic].append(score)
                        
                        topic_averages = {topic: sum(scores)/len(scores) for topic, scores in topic_scores.items()}
                        
                        st.markdown("**📋 주제별 평균 점수:**")
                        for topic, avg_score in sorted(topic_averages.items(), key=lambda x: x[1], reverse=True):
                            count = len(topic_scores[topic])
                            st.write(f"• **{topic}** - 평균 {avg_score:.1f}점 ({count}회 작성)")
                        
                        best_topic = max(topic_averages.items(), key=lambda x: x[1])
                        worst_topic = min(topic_averages.items(), key=lambda x: x[1])
                        
                        col_analysis1, col_analysis2 = st.columns(2)
                        with col_analysis1:
                            st.success(f"🏆 **가장 잘한 주제**\n{best_topic[0]} ({best_topic[1]:.1f}점)")
                        with col_analysis2:
                            st.warning(f"💪 **개선 필요 주제**\n{worst_topic[0]} ({worst_topic[1]:.1f}점)")
                    
                    else:
                        st.info("🎯 주제별 분석을 보려면 논술문을 작성해주세요.")
            
            st.sidebar.markdown("---")
            st.sidebar.markdown("### 📊 빠른 통계")
            try:
                user_essays, _ = get_user_essays(st.session_state.username)
                if user_essays:
                    stats = calculate_user_stats(user_essays)
                    st.sidebar.metric("작성한 논술", f"{stats['total_essays']}편")
                    st.sidebar.metric("평균 점수", f"{stats['average_score']}점")
                else:
                    st.sidebar.info("아직 작성한 논술이 없습니다.")
            except:
                st.sidebar.info("통계를 불러오는 중...")
            
            st.sidebar.markdown("---")

if __name__ == "__main__":
    main()