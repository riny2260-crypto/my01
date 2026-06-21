import streamlit as st
import fitz  # PyMuPDF (PDF 분석용 도구)
import os
import re
import pandas as pd
from datetime import datetime

# 🌟 구글 드라이브 클라우드 소통용 특수 도구들
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

# =========================================================================
# [설정 사항] 우리 학교 환경에 맞게 이름과 키워드만 관리하는 구역입니다.
# =========================================================================

# 1. 우리 학교 전체 선생님 명단
ALL_TEACHERS = ["김철수", "이영희", "박민수", "최수연", "정우성", "홍길동", "조서린"]

# 2. 인식할 연수명 힌트 단어(키워드) 주머니
TRAINING_KEYWORDS = {
    "다문화이해교육": ["다문화", "상호문화", "다문화이해"],
    "성희롱예방교육": ["성희롱", "폭력예방", "양성평등", "4대폭력"],
    "안전보건교육": ["안전보건", "산업안전", "중대재해"],
    "학교폭력예방교육": ["학교폭력", "학폭예방"],
    "아동학대예방교육": ["아동학대", "학대신고"],
    "개인정보보호교육": ["개인정보", "정보보안"],
    "청렴교육": ["부패방지", "청렴", "이해충돌"],
    "긴급복지신고의무자교육": ["긴급복지", "긴급", "신고의무자"]
}

# 3. 구글 드라이브 클라우드 접근 권한 주소 (읽기/쓰기 전체 권한)
SCOPES = ['https://www.googleapis.com/auth/drive']


# =========================================================================


# 🔑 [구글 클라우드 로그인 인증 마법 함수]
def get_gdrive_service():
    creds = None
    # 이미 한 번 로그인해서 열쇠(token.json)를 받아둔 적이 있다면 바로 가져와!
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    # 열쇠가 없거나 만료되었다면 새로 로그인 화면을 띄워라!
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 1단계에서 다운받아 폴더에 넣어둔 client_secret.json을 읽어와서 로그인창 오픈!
            flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # 로그인이 끝나면 다음부턴 로그인창 안 뜨게 'token.json'으로 저장해둬!
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)


# 📂 [구글 클라우드 내부에 폴더가 있으면 ID를 찾고, 없으면 새로 만드는 함수]
def get_or_create_drive_folder(service, folder_name, parent_id=None):
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(q=query, fields="files(id)").execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']  # 이미 구글 드라이브에 폴더가 존재하면 그 고유 ID 번호를 반환!
    else:
        # 없으면 구글 드라이브 클라우드 세상에 새 폴더를 생성해!
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')


# 🔍 [1. PDF 상세 정보 분석 함수] (기존과 동일)
def analyze_pdf_details(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    full_text = "".join([page.get_text() for page in doc])

    # 1) 성함 추출
    detected_name = "미확인이름"
    for name in ALL_TEACHERS:
        if name in full_text:
            detected_name = name
            break

    # 2) 여러 연수명 추출 (중복 매칭 허용)
    detected_courses = []
    for course_name, keywords in TRAINING_KEYWORDS.items():
        if any(keyword in full_text for keyword in keywords):
            detected_courses.append(course_name)
    if not detected_courses:
        detected_courses.append("기타연수")

    # 3) 이수번호 추출
    serial_match = re.search(r'(제\s*[\w\s-]+(?:호|호\b))', full_text)
    detected_serial = serial_match.group(1).strip() if serial_match else "미확인(이수번호)"

    # 4) 연수 기간 추출
    date_pattern = r'(\d{4}[.\s년-]\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?\.?\s*(?:~|-)\s*\(?\d{4}[.\s년-]\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?\.?)'
    date_match = re.search(date_pattern, full_text)
    detected_period = date_match.group(1).strip() if date_match else "미확인(연수기간)"

    # 5) 이수 시간 추출
    time_match = re.search(r'(\d+\s*시간\s*\d*\s*분?|\d+\s*시간)', full_text)
    detected_time = time_match.group(1).strip() if time_match else "미확인(이수시간)"

    return detected_name, detected_courses, detected_serial, detected_period, detected_time


# 📊 [2. 클라우드 전용 구글 드라이브 내부 CSV 취합장부 업데이트 함수]
def update_csv_ledger(service, course_folder_id, course_name, data_row):
    filename = f"{course_name}_취합장부.csv"
    query = f"name = '{filename}' and '{course_folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    items = results.get('files', [])

    new_df = pd.DataFrame([data_row])

    if items:
        # 장부가 구글 드라이브에 이미 있으면 다운로드해서 기존 내용 뒤에 새 줄 붙이기!
        file_id = items[0]['id']
        file_content = service.files().get_media(fileId=file_id).execute()
        existing_df = pd.read_csv(io.BytesIO(file_content))

        if data_row["선생님 성함"] in existing_df["선생님 성함"].values:
            existing_df = existing_df[existing_df["선생님 성함"] != data_row["선생님 성함"]]
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        # 다시 구글 클라우드로 덮어쓰기 전송!
        csv_buffer = io.BytesIO()
        combined_df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_buffer.seek(0)

        media = MediaIoBaseUpload(csv_buffer, mimetype='text/csv', resumable=True)
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        # 장부 파일이 아예 없다면 가상 메모리에 새 표를 짜서 구글 드라이브로 최초 전송!
        csv_buffer = io.BytesIO()
        new_df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_buffer.seek(0)

        file_metadata = {
            'name': filename,
            'parents': [course_folder_id]
        }
        media = MediaIoBaseUpload(csv_buffer, mimetype='text/csv', resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()


# 🌐 [3. 웹 화면 구성 (Streamlit)]
st.set_page_config(page_title="연수 이수증 자동 분류기", layout="wide")
st.title("📄 연수 이수증 자동 분류 & 장부 자동 생성 프로그램")
st.markdown("---")

# 실시간 제출자 기록용 임시 메모리 장방 만들기
if "course_submissions" not in st.session_state:
    st.session_state.course_submissions = {}

for course in list(TRAINING_KEYWORDS.keys()) + ["기타연수"]:
    if course not in st.session_state.course_submissions:
        st.session_state.course_submissions[course] = set()

menu = st.sidebar.radio("메뉴 선택", ["이수증 업로드", "미제출자 확인"])

# [메뉴 1: 이수증 업로드 탭]
if menu == "이수증 업로드":
    st.header("📥 이수증 업로드 및 정보 추출")
    st.write("선생님들의 이수증(PDF) 파일을 업로드하면 파일 분류와 장부 작성이 동시에 진행됩니다.")

    uploaded_files = st.file_uploader(
        "PDF 파일을 선택하거나 이 창으로 드래그해 주세요. (다중 선택 가능)",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded_files and st.button("파일 분석 및 구글 드라이브 전송 시작"):
        try:
            # 구글 로그인 로봇 깨우기
            drive_service = get_gdrive_service()

            # 구글 드라이브 최상위에 [연수이수증_취합소] 메인 폴더가 없으면 만들고 ID 가져오기
            root_folder_id = get_or_create_drive_folder(drive_service, "연수이수증_취합소")
            success_count = 0

            for uploaded_file in uploaded_files:
                file_bytes = uploaded_file.read()
                # 1번 분석 로봇 시켜서 PDF 글자 따오기
                name, courses, serial, period, itime = analyze_pdf_details(file_bytes)

                if name == "미확인이름":
                    st.warning(f"⚠️ '{uploaded_file.name}' 파일에서 등록된 선생님 이름을 찾을 수 없어 건너뜁니다.")
                    continue

                is_integrated = "통합 연수" if len(courses) >= 2 else "-"
                info_data = {
                    "선생님 성함": name,
                    "이수번호": serial,
                    "연수 기간": period,
                    "이수 시간": itime,
                    "비고": is_integrated,
                    "제출 일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }

                saved_folders = []
                for course in courses:
                    # 메인 폴더 안에 개별 연수 이름으로 새 하위 폴더 생성 또는 ID 조회
                    course_folder_id = get_or_create_drive_folder(drive_service, course, parent_id=root_folder_id)

                    # 파일명 빌드: (연수과정명)_선생님이름.pdf
                    new_filename = f"({course})_{name}.pdf"
                    file_metadata = {
                        'name': new_filename,
                        'parents': [course_folder_id]
                    }
                    # 🌟 [mimetype 수정 완료!] 에러 유발 단어 완벽 교체
                    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype='application/pdf', resumable=True)

                    # 구글 드라이브 클라우드 폴더 속으로 PDF 복사본 쏘기!
                    drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

                    # 2번 로봇 시켜서 폴더 안에 있는 엑셀 취합장부(CSV)도 실시간 갱신하기!
                    update_csv_ledger(drive_service, course_folder_id, course, info_data)

                    saved_folders.append(course)

                    # 실시간 대조 장부용 세션 상태 업데이트
                    if course in st.session_state.course_submissions:
                        st.session_state.course_submissions[course].add(name)
                    else:
                        st.session_state.course_submissions["기타연수"].add(name)

                with st.expander(f"✅ {name} 선생님 클라우드 전송 완료 (추출 정보 확인)"):
                    st.write(f"• 드라이브 저장 폴더: {', '.join(saved_folders)}")
                    st.text(f"• 이수번호: {serial}\n• 연수기간: {period}\n• 이수시간: {itime}\n• 과정구분: {is_integrated}")

                success_count += 1

            if success_count > 0:
                st.balloons()
                st.success(f"🎉 총 {success_count}명의 이수증이 구글 드라이브 클라우드로 안전하게 업로드 및 분류 장부 반영 완료되었습니다!")

        except Exception as e:
            st.error(f"⚠️ 구글 API 연결 중 오류 발생: {e}")

# [메뉴 2: 미제출자 확인 탭]
elif menu == "미제출자 확인":
    st.header("🔍 연수 과정별 미제출자 현황")
    st.write("조회하고 싶은 연수 과정을 선택하시면 해당 교육의 미제출자 명단을 실시간 대조하여 보여줍니다.")

    course_options = list(TRAINING_KEYWORDS.keys()) + ["기타연수"]
    selected_course = st.selectbox("📚 확인하실 연수 과정을 선택하세요", course_options)

    st.markdown(f"### 📋 '{selected_course}' 현황 확인")

    if selected_course not in st.session_state.course_submissions:
        st.session_state.course_submissions[selected_course] = set()

    submitted = st.session_state.course_submissions[selected_course]
    unsubmitted = [teacher for teacher in ALL_TEACHERS if teacher not in submitted]

    col1, col2 = st.columns(2)

    with col1:
        st.subheader(f"🟢 제출 완료 ({len(submitted)}명)")
        if submitted:
            for t in sorted(list(submitted)):
                st.write(f"- {t} ✔️")
        else:
            st.write("_아직 이 연수 과정에 제출된 이수증이 없습니다._")

    with col2:
        st.subheader(f"🔴 미제출 선생님 ({len(unsubmitted)}명)")
        if unsubmitted:
            for t in sorted(unsubmitted):
                st.write(f"- **{t}**")
        else:
            st.success(f"🎉 전원 제출! 모든 선생님이 '{selected_course}' 이수증을 제출하셨습니다!")