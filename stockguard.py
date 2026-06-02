import streamlit as st
from google import genai
from google.genai import types
import requests
from bs4 import BeautifulSoup
from datetime import datetime

today = datetime.now().strftime("%Y년 %m월 %d일")
# ==========================================
# 1. 초기 설정 및 API 키 세팅
# ==========================================
st.set_page_config(page_title="주식 사기 방지 필터링 챗봇", page_icon="🛡️", layout="centered")

API_KEY = st.secrets["GEMINI_API_KEY"]
@st.cache_resource
def get_genai_client():
    return genai.Client(api_key=API_KEY)

client = get_genai_client()

if "user_level" not in st.session_state:
    st.session_state.user_level = "초보 (주린이, 용어 모름)"
if "last_analysis" not in st.session_state:
    st.session_state.last_analysis = ""

# ==========================================
# 2. 유틸리티 함수
# ==========================================
def get_text_from_response(response):
    """구글 검색 툴 사용 시 response에서 안전하게 텍스트만 추출하는 함수"""
    result_text = ""
    try:
        # response -> candidates -> content -> parts 순서로 접근
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                # part 객체에 text 속성이 있는지 확인하고 합침
                if hasattr(part, "text") and part.text:
                    result_text += part.text
        return result_text
    except Exception as e:
        return f"텍스트 추출 중 오류 발생: {e}"

def extract_text_from_url(url):
    try:
        response = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style"]):
            script.extract()
        return soup.get_text(separator=' ', strip=True)[:3000]
    except Exception as e:
        return f"URL 분석 실패: {e}"

def keyword_risk_check(text):
    risk_keywords = ["보장", "작전", "VIP", "상한가", "당장", "원금", "무조건", "폭등", "대박","빨리"]
    found_keywords = [kw for kw in risk_keywords if kw in text]
    return found_keywords

# ==========================================
# 3. 메인 UI 및 로직
# ==========================================
st.title("🛡️ 주식 정보 사기 방지 챗봇")

st.session_state.user_level = st.radio(
    "투자 경험 수준 선택",
    ("초보 (주린이, 용어 모름)", "중급 (기본 용어 이해)", "고급 (재무제표 분석 가능)"),
    horizontal=True
)

st.divider()

user_input = st.text_area("분석할 텍스트 또는 URL 입력", height=150)

if st.button("분석 시작", type="primary"):
    if user_input:
        with st.spinner("실시간 구글 검색 데이터를 대조하여 분석 중입니다..."):
            target_text = user_input
            if user_input.startswith("http"):
                target_text = extract_text_from_url(user_input)
                st.info("🔗 URL 본문 추출 완료")

            found_keywords = keyword_risk_check(target_text)
            if found_keywords:
                st.error(f"⚠️ 위험 글 가능성 포착! 위험 키워드 감지: {', '.join(found_keywords)}")
            else:
                st.success("✅ 1차 키워드 분석 안전 | 위험 키워드 감지 안됨")
            
            prompt = f"""
            오늘 날짜는 {today}입니다.
            당신은 금융 전문가입니다. 사용자 수준('{st.session_state.user_level}')에 맞춰 설명하세요. 
            오늘 날짜를 기준으로 설명해야하며, 주식과 주가도 오늘 날짜 기준의 최신 내용으로 업데이트하여 정보를 제공해야됩니다.
            [분석 내용]: {target_text}
            반드시 google search 기능을 활용해 글의 정보의 사실 여부를 공시/뉴스로 교차 검증하세요.
            주가 관련 내용이 존재하면 반드시 google search를 사용하여 현재 공식 주식 정보를 바탕으로 사용자에게 정보를 제공하세요.
            
            규칙:
            1. 구글 검색 기반 팩트 체크 결과를 최우선으로 작성.
            2. 위험 표현은 <span style="color:red; font-weight:bold;">텍스트</span>로 감싸기.
            3. 주관적 표현은 <span style="color:orange; font-weight:bold;">텍스트</span>로 감싸기.
            4. 답변의 가장 마지막에는 반드시 `> **최종 결론:** 지금 바로 투자하기에는 위험해 보입니다.` 혹은 `객관적인 팩트가 섞여있으나 주관적인 부분은 잘 판단해야합니다.` 와 같은 형식의 직관적인 한 줄 요약 박스를 제공하세요. 제가 드린 예시 외에도 비슷한 뉘앙스로 정보글마다 판단하여 요약 박스를 제공하세요.
            5. 전체적인 분석 글을 작성하기에 앞서 제일 먼저 간단하게 결론 요약을 한줄로 해야합니다. 최종 결론을 간단하게 2-3줄만 요약하면 됩니다.
            
            """
            
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash', # 혹은 gemini-2.0-pro-exp-02-05 (최신모델)
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())]
                    )
                )
                
                # [수정 포인트] 안전하게 텍스트 추출 함수 호출
                final_text = get_text_from_response(response)
                st.session_state.last_analysis = final_text
                
                st.subheader("📊 AI 실시간 교차 검증 결과")
                st.markdown(st.session_state.last_analysis, unsafe_allow_html=True)
                
            except Exception as e:
                st.error(f"분석 중 오류: {e}")

# ==========================================
# 4. 더 쉽게 설명하기
# ==========================================
if st.session_state.last_analysis:
    st.divider()
    if st.button("더 쉽게 설명해주세요 🥺"):
        with st.spinner("비유를 들어 재작성 중..."):
            easier_prompt = f"다음 내용을 사회 초년생도 이해하게 쉬운 비유로 설명해줘. 예를 들어 영업이익 하락은 회사가 벌어들이는 돈이 줄다 이런식으로. : {st.session_state.last_analysis}"
            try:
                response_easy = client.models.generate_content(
                    model='gemini-2.0-flash',
                    contents=easier_prompt
                )
                # 재설명 결과도 안전하게 추출
                final_easy_text = get_text_from_response(response_easy)
                st.success("✨ 쉬운 설명")
                st.markdown(final_easy_text, unsafe_allow_html=True)
            except Exception as e:
                st.error(f"재설명 오류: {e}")
