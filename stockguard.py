import streamlit as st
from google import genai
from google.genai import types
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import time
import re  # [추가] 위험도 태그 파싱용

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

# [변경] 결과를 세션 상태에 모아두고, 화면 렌더링은 버튼 클릭과 분리.
#  → '더 쉽게 설명'을 눌러 화면이 새로고침돼도 분석 결과/신호등/출처가 사라지지 않음(기존 잠재 버그 수정).
if "user_level" not in st.session_state:
    st.session_state.user_level = "초보 (주린이, 용어 모름)"
if "last_analysis" not in st.session_state:
    st.session_state.last_analysis = ""
if "found_keywords" not in st.session_state:
    st.session_state.found_keywords = []
if "risk_level" not in st.session_state:
    st.session_state.risk_level = None
if "sources" not in st.session_state:
    st.session_state.sources = []
if "easy_explanation" not in st.session_state:
    st.session_state.easy_explanation = ""

# ==========================================
# 2. 유틸리티 함수
# ==========================================
def get_text_from_response(response):
    """구글 검색 툴 사용 시 response에서 안전하게 텍스트만 추출하는 함수"""
    result_text = ""
    try:
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "text") and part.text:
                    result_text += part.text
        return result_text
    except Exception as e:
        return f"텍스트 추출 중 오류 발생: {e}"

def extract_text_from_url(url):
    """URL 본문 텍스트를 추출(최대 3000자). 입력이 URL일 때 사용."""
    try:
        response = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        for script in soup(["script", "style"]):
            script.extract()
        return soup.get_text(separator=' ', strip=True)[:3000]
    except Exception as e:
        return f"URL 분석 실패: {e}"

def extract_sources(response):
    """[추가] Google Search 교차검증에 실제로 사용된 출처(공시/뉴스) 링크를 추출.
    → 테스트에서 고급 사용자가 '검증해준다면서 출처가 없다'고 지적한 문제 해결."""
    sources = []
    try:
        cand = response.candidates[0]
        gm = getattr(cand, "grounding_metadata", None)
        chunks = getattr(gm, "grounding_chunks", None) if gm else None
        if chunks:
            for ch in chunks:
                web = getattr(ch, "web", None)
                uri = getattr(web, "uri", None) if web else None
                if uri:
                    title = getattr(web, "title", None) or uri
                    sources.append((title, uri))
    except Exception:
        pass
    # 중복 제거
    seen, uniq = set(), []
    for t, u in sources:
        if u not in seen:
            seen.add(u)
            uniq.append((t, u))
    return uniq

def keyword_risk_check(text):
    # [변경] 키워드 목록 확장. 기존엔 정교한 찌라시에서 '빨리' 하나만 걸려
    #  거의 '안전'처럼 보이는 착시가 있었음(테스트 발견 사항).
    risk_keywords = [
        "보장", "작전", "VIP", "상한가", "당장", "원금", "무조건", "폭등", "대박", "빨리",
        "매집", "단기", "급등", "세력", "내부정보", "내부 정보", "리딩", "막차",
        "확실", "강추", "선취매", "수익 인증", "치고 올라",
    ]
    found_keywords = [kw for kw in risk_keywords if kw in text]
    return found_keywords

def parse_risk_level(text):
    """[추가] AI 응답 첫 줄의 [RISK:HIGH/MEDIUM/LOW] 태그를 파싱하고 본문에서 제거."""
    m = re.search(r"\[RISK:(HIGH|MEDIUM|LOW)\]", text)
    level = m.group(1) if m else None
    cleaned = re.sub(r"\[RISK:(HIGH|MEDIUM|LOW)\]\s*", "", text, count=1).lstrip()
    return level, cleaned

def compute_signal(risk_level, found_keywords):
    """[추가] AI 판단(주)과 키워드(보조)를 하나의 신호등으로 통합.
    → 테스트에서 '위 키워드 경고와 아래 AI 결론이 따로 논다'는 혼란 해결.
       키워드가 있으면 절대 초록(안전)으로 내려가지 않도록 보정."""
    if risk_level == "HIGH":
        return "🔴", "위험 높음", "지금 투자에 매우 위험해 보입니다. 권유·조급함을 부추기는 표현이 많아요.", "#ffe5e5"
    if risk_level == "MEDIUM":
        return "🟡", "주의 필요", "객관적 사실과 부추기는 표현이 섞여 있어요. 신중히 판단하세요.", "#fff4d6"
    if risk_level == "LOW":
        if found_keywords:
            return "🟡", "주의 필요", "큰 위험 신호는 적지만 일부 자극적 표현이 감지됐어요.", "#fff4d6"
        return "🟢", "비교적 안전", "뚜렷한 위험 신호는 없어요. 그래도 최종 판단은 본인 몫이에요.", "#e6f7ec"
    # 태그 파싱 실패 시 키워드 기반 폴백
    if found_keywords:
        return "🟡", "주의 필요", "자극적 표현이 감지됐어요. 내용을 꼼꼼히 확인하세요.", "#fff4d6"
    return "⚪", "분석 완료", "아래 상세 분석을 확인하세요.", "#eef0f2"

# ==========================================
# 3. 메인 UI 및 로직
# ==========================================
st.title("🛡️ 주식 정보 사기 방지 챗봇")

# [변경] 수준 선택 온보딩 강화 — 각 수준이 설명을 어떻게 바꾸는지 미리 보여줌.
#  → 테스트에서 수준을 잘못 골랐다가 다시 실행한 사용자 문제 완화.
st.session_state.user_level = st.radio(
    "투자 경험 수준 선택",
    ("초보 (주린이, 용어 모름)", "중급 (기본 용어 이해)", "고급 (재무제표 분석 가능)"),
    captions=[
        "예) '공시' → '회사가 올린 공식 공지글'처럼 풀어서 설명",
        "예) 기본 용어는 그대로, 어려운 용어만 괄호 설명",
        "예) 전문 용어·재무 데이터 중심으로 분석",
    ],
    horizontal=True,
)

st.divider()

user_input = st.text_area("분석할 텍스트 또는 URL 입력", height=150)

if st.button("분석 시작", type="primary"):
    if user_input:
        # ==========================================
        # 단계별 상태 표시 + 진행률 바
        #  → 테스트에서 '로딩이 멈춘 줄 알고 다시 누를 뻔했다'는 문제 해결.
        # ==========================================
        with st.status("🔍 분석을 시작합니다...", expanded=True) as status:
            progress = st.progress(0, text="입력 정보를 수집하는 중...")

            # STEP 1: URL or 텍스트 처리
            st.write("📥 입력 정보를 수집하는 중...")
            time.sleep(0.4)
            target_text = user_input
            if user_input.startswith("http"):
                target_text = extract_text_from_url(user_input)
                st.write("🔗 URL 본문 추출 완료")
            progress.progress(20, text="입력 정보 수집 완료")
            time.sleep(0.4)

            # STEP 2: 1차 키워드 필터링
            st.write("🔎 위험 키워드 1차 필터링 중...")
            found_keywords = keyword_risk_check(target_text)
            progress.progress(45, text="키워드 필터링 완료")
            time.sleep(0.4)

            # STEP 3: 구글 검색 기반 AI 분석
            st.write("🌐 실시간 뉴스 및 공시 데이터 수집 중...")
            progress.progress(65, text="실시간 뉴스·공시 수집 중...")
            time.sleep(0.4)

            st.write("🤖 AI가 정보의 사실 여부를 교차 검증하는 중... (수 초~십여 초 걸릴 수 있어요. 멈춘 게 아니에요!)")
            progress.progress(80, text="AI 교차 검증 중... 잠시만 기다려 주세요!")

            # [변경] 선택된 수준의 지침만 동적으로 주입(세 수준을 한꺼번에 나열하면
            #  모델이 난이도를 섞어버려 초보 설명이 어려워지던 문제 해결).
            level_guides = {
                "초보 (주린이, 용어 모름)": (
                    "사용자는 금융을 전혀 모르는 '완전 초보'입니다. 다음을 가장 우선해서 끝까지 지키세요.\n"
                    "  - 금융 전문 용어(공시·순매수·매집·영업이익·HBM·밸류에이션 등)를 그냥 쓰지 마세요. "
                    "교차검증 과정에서 이런 용어가 나오면, 그 자리에서 바로 일상 비유로 풀어 쓰세요. "
                    "예: '공시' 대신 '회사가 공식 홈페이지에 올리는 공지글', "
                    "'외국인 순매수' 대신 '외국 투자자들이 파는 것보다 더 많이 사들이고 있다는 뜻'.\n"
                    "  - 모든 문장은 짧고 단순하게, 중학생도 이해할 수준으로 씁니다.\n"
                    "  - 숫자·비율이 나오면 '그게 결국 무슨 뜻인지'를 한 문장으로 덧붙입니다.\n"
                    "  - 어쩔 수 없이 어려운 단어를 한 번 썼다면 반드시 괄호로 쉬운 풀이를 답니다."
                ),
                "중급 (기본 용어 이해)": (
                    "기본 용어(매수·매도·공시 등)는 그대로 쓰되, 전문 재무 용어는 괄호로 짧게 풀이를 덧붙이세요."
                ),
                "고급 (재무제표 분석 가능)": (
                    "전문 금융 용어를 자유롭게 쓰고, 재무적 근거와 데이터 중심으로 분석하세요."
                ),
            }
            level_instruction = level_guides.get(st.session_state.user_level, "")

            prompt = f"""
            오늘 날짜는 {today}입니다.
            당신은 금융 전문가입니다.

            사용자 수준은 '{st.session_state.user_level}'입니다. 아래 설명 지침을 다른 어떤 규칙보다 우선해서 끝까지 지키세요:
            {level_instruction}

            오늘 날짜를 기준으로 설명해야하며, 주식과 주가도 오늘 날짜 기준의 최신 내용으로 업데이트하여 정보를 제공해야됩니다.
            [분석 내용]: {target_text}
            반드시 google search 기능을 활용해 글의 정보의 사실 여부를 공시/뉴스로 교차 검증하세요.
            주가 관련 내용이 존재하면 반드시 google search를 사용하여 현재 공식 주식 정보를 바탕으로 사용자에게 정보를 제공하세요.

            규칙:
            0. (매우 중요) 응답의 가장 첫 줄에는 다른 어떤 글자보다 앞서 `[RISK:HIGH]`, `[RISK:MEDIUM]`, `[RISK:LOW]` 중 하나만 단독으로 출력하세요. 사기·과장·권유가 강하면 HIGH, 사실과 주관이 섞였으면 MEDIUM, 위험 신호가 거의 없으면 LOW입니다. 이 태그는 1차 키워드 결과가 아니라 당신의 종합 판단을 반영해야 합니다.
            1. 구글 검색 기반 팩트 체크 결과를 최우선으로 작성.
            2. 위험 표현은 <span style="color:red; font-weight:bold;">텍스트</span>로 감싸기.
            3. 주관적 표현은 <span style="color:orange; font-weight:bold;">텍스트</span>로 감싸기.
            4. 답변의 가장 마지막에는 반드시 `> **최종 결론:** 지금 바로 투자하기에는 위험해 보입니다.` 혹은 `객관적인 팩트가 섞여있으나 주관적인 부분은 잘 판단해야합니다.` 와 같은 형식의 직관적인 한 줄 요약 박스를 제공하세요. 제가 드린 예시 외에도 비슷한 뉘앙스로 정보글마다 판단하여 요약 박스를 제공하세요.
            5. RISK 태그 바로 다음 줄에, 전체 분석에 앞서 결론을 간단하게 2-3줄로 먼저 요약하세요.
            """

            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())]
                    )
                )

                final_text = get_text_from_response(response)

                # [추가] 위험도 태그 파싱 + 출처 추출 후 세션 상태에 저장
                risk_level, cleaned_text = parse_risk_level(final_text)
                st.session_state.risk_level = risk_level
                st.session_state.last_analysis = cleaned_text
                st.session_state.found_keywords = found_keywords
                st.session_state.sources = extract_sources(response)
                st.session_state.easy_explanation = ""  # 새 분석 시 이전 쉬운 설명 초기화

                # STEP 4: 최종 판단 완료
                progress.progress(100, text="분석 완료!")
                st.write("⚖️ 최종 판단 완료!")
                time.sleep(0.3)

                status.update(label="✅ 분석 완료!", state="complete", expanded=False)

            except Exception as e:
                status.update(label="❌ 분석 중 오류 발생", state="error", expanded=True)
                st.error(f"분석 중 오류: {e}")
                st.stop()
    else:
        st.warning("분석할 텍스트나 URL을 먼저 입력해 주세요.")

# ==========================================
# 4. 결과 렌더링 (세션 상태 기반 — 버튼 재클릭에도 유지)
# ==========================================
if st.session_state.last_analysis:

    # ---- (1) 통합 위험도 신호등: 화면 최상단에 한눈에 ----
    emoji, label, msg, bg = compute_signal(
        st.session_state.risk_level, st.session_state.found_keywords
    )
    st.markdown(
        f"""
        <div style="background:{bg}; border-radius:12px; padding:16px 18px; margin-bottom:6px; color:#111;">
          <div style="font-size:22px; font-weight:800;">{emoji} 종합 위험도: {label}</div>
          <div style="font-size:15px; margin-top:6px; line-height:1.5;">{msg}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ---- (2) '더 쉽게 설명' 버튼: 결과 상단으로 이동 + 눈에 띄게 ----
    #  → 테스트에서 '버튼이 맨 아래 있어 못 찾았다'는 문제 해결.
    if st.button("🥺 내용이 어렵나요? 더 쉽게 설명해주세요", use_container_width=True):
        with st.status("✏️ 쉬운 설명으로 재작성 중...", expanded=True) as status_easy:
            st.write("📖 어려운 용어를 쉬운 표현으로 바꾸는 중...")
            time.sleep(0.5)
            st.write("🖊️ 비유 문장 생성 중...")

            easier_prompt = f"다음 내용을 사회 초년생도 이해하게 쉬운 비유로 설명해줘. 예를 들어 영업이익 하락은 회사가 벌어들이는 돈이 줄다 이런식으로. : {st.session_state.last_analysis}"
            try:
                response_easy = client.models.generate_content(
                    model='gemini-3.1-flash-lite',
                    contents=easier_prompt
                )
                st.session_state.easy_explanation = get_text_from_response(response_easy)
                status_easy.update(label="✅ 쉬운 설명 완료!", state="complete", expanded=False)
            except Exception as e:
                status_easy.update(label="❌ 오류 발생", state="error", expanded=True)
                st.error(f"재설명 오류: {e}")

    if st.session_state.easy_explanation:
        st.success("✨ 쉬운 설명")
        st.markdown(st.session_state.easy_explanation, unsafe_allow_html=True)

    # ---- (3) AI 상세 교차 검증 결과 ----
    st.subheader("📊 AI 실시간 교차 검증 결과")
    st.markdown(st.session_state.last_analysis, unsafe_allow_html=True)

    # ---- (4) 근거 출처: 실제 검증에 쓰인 공시/뉴스 링크 ----
    if st.session_state.sources:
        with st.expander(f"🔗 검증에 사용된 출처 {len(st.session_state.sources)}개 보기"):
            for title, uri in st.session_state.sources:
                st.markdown(f"- [{title}]({uri})")
    else:
        st.caption("ℹ️ 이번 분석에서는 인용 가능한 출처 링크를 가져오지 못했어요. 결론은 위 분석 내용을 참고하세요.")

    # ---- (5) 1차 키워드 필터 상세(보조 정보) ----
    #  → 단독 '안전' 배너로 띄우지 않고, 보조 근거로 강등해 신호등과의 충돌 제거.
    with st.expander("🔎 1차 키워드 필터 상세"):
        if st.session_state.found_keywords:
            st.write("감지된 자극·권유성 키워드: " + ", ".join(st.session_state.found_keywords))
        else:
            st.write(
                "감지된 키워드는 없습니다. 다만 키워드가 없다고 안전한 글은 아니에요. "
                "정교한 사기 글일수록 키워드를 피해 가므로, 위의 **종합 위험도**를 기준으로 판단하세요."
            )
