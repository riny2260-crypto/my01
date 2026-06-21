import streamlit as st
import fitz
import os
import re
import pandas as pd
from datetime import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

ALL_TEACHERS = ["김철수", "이영희", "박민수", "최수연", "정우성", "홍길동", "조서린"]

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

SCOPES = ['https://www.googleapis.com/auth/drive']


def get_gdrive_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if 'gdrive_secrets' in st.secrets:
                client_config = {
                    "installed": {
                        "client_id": st.secrets["gdrive_secrets"]["client_id"],
                        "client_secret": st.secrets["gdrive_secrets"]["client_secret"],
                        "project_id": st.secrets["gdrive_secrets"]["project_id"],
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"]
                    }
                }
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
                st.markdown(f"[🔗 여기를 클릭하여 구글 계정 로그인을 완료해 주세요]({auth_url})")
                code = st.text_input("인증 후 브라우저 주소창의 'code=' 뒤에 나오는 문자열을 입력해 주세요:")
                if not code:
                    st.info("구글 인증이 필요합니다. 위 링크에서 로그인 후 코드를 복사해 입력창에 넣어주세요.")
                    st.stop()
                flow.fetch_token(code=code)
                creds = flow.credentials
            elif os.path.exists('client_secret.json'):
                flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                raise FileNotFoundError("Authentication keys not found.")
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)


def get_or_create_drive_folder(service, folder_name, parent_id=None):
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    else:
        query += " and 'root' in parents"

    results = service.files().list(q=query, fields="files(id)").execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']
    else:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        else:
            file_metadata['parents'] = ['root']
        folder = service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')


def analyze_pdf_details(file_bytes):
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    full_text = "".join([page.get_text() for page in doc])

    detected_name = "미확인이름"
    for name in ALL_TEACHERS:
        if name in full_text:
            detected_name = name
            break

    detected_courses = []
    for course_name, keywords in TRAINING_KEYWORDS.items():
        if any(keyword in full_text for keyword in keywords):
            detected_courses.append(course_name)
    if not detected_courses:
        detected_courses.append("기타연수")

    serial_match = re.search(r'(제\s*[\w\s-]+(?:호|호\b))', full_text)
    detected_serial = serial_match.group(1).strip() if serial_match else "미확인(이수번호)"

    date_pattern = r'(\d{4}[.\s년-]\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?\.?\s*(?:~|-)\s*\(?\d{4}[.\s년-]\s*\d{1,2}[.\s월-]\s*\d{1,2}[일]?\.?)'
    date_match = re.search(date_pattern, full_text)
    detected_period = date_match.group(1).strip() if date_match else "미확인(연수기간)"

    time_match = re.search(r'(\d+\s*시간\s*\d*\s*분?|\d+\s*시간)', full_text)
    detected_time = time_match.group(1).strip() if time_match else "미확인(이수시간)"

    return detected_name, detected_courses, detected_serial, detected_period, detected_time


def update_csv_ledger(service, course_folder_id, course_name, data_row):
    filename = f"{course_name}_취합장부.csv"
    query = f"name = '{filename}' and '{course_folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    items = results.get('files', [])

    new_df = pd.DataFrame([data_row])

    if items:
        file_id = items[0]['id']
        file_content = service.files().get_media(fileId=file_id).execute()
        existing_df = pd.read_csv(io.BytesIO(file_content))

        if data_row["선생님 성함"] in existing_df["선생님 성함"].values:
            existing_df = existing_df[existing_df["선생님 성함"] != data_row["선생님 성함"]]
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)

        csv_buffer = io.BytesIO()
        combined_df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_buffer.seek(0)

        media = MediaIoBaseUpload(csv_buffer, mimetype='text/csv', resumable=True)
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        csv_buffer = io.BytesIO()
        new_df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        csv_buffer.seek(0)

        file_metadata = {
            'name': filename,
            'parents': [course_folder_id]
        }
        media = MediaIoBaseUpload(csv_buffer, mimetype='text/csv', resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()


st.set_page_config(page_title="연수 이수증 자동 분류기", layout="wide")
st.title("📄 연수 이수증 자동 분류 & 장부 자동 생성 프로그램")
st.markdown("---")

if "course_submissions" not in st.session_state:
    st.session_state.course_submissions = {}

for course in list(TRAINING_KEYWORDS.keys()) + ["기타연수"]:
    if course not in st.session_state.course_submissions:
        st.session_state.course_submissions[course] = set()

menu = st.sidebar.radio("메뉴 선택", ["이수증 업로드", "미제출자 확인"])

if menu == "이수증 업로드":
    st.header("📥 이수증 업로드 및 정보 추출")
    st.write("선생님들의 이수증(PDF) 파일을 업로드하면 파일 분류와 장부 작성이 동시에 진행됩니다.")

    uploaded_files = st.file_uploader(
        "PDF 파일을 선택하거나 이 창으로 드래그해 주세요. (다중 선택 가능)",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded_files:
        if st.button("파일 분석 및 구글 드라이브 전송 시작"):
            try:
                drive_service = get_gdrive_service()
                root_folder_id = get_or_create_drive_folder(drive_service, "연수이수증_취합소")
                success_count = 0

                for uploaded_file in uploaded_files:
                    file_bytes = uploaded_file.read()
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
                        course_folder_id = get_or_create_drive_folder(drive_service, course, parent_id=root_folder_id)
                        new_filename = f"({course})_{\n                        name}.pdf"
                        file_metadata = {
                            'name': new_filename,
                            'parents': [course_folder_id]
                        }
                        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype='application/pdf', resumable=True)
                        drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                        update_csv_ledger(drive_service, course_folder_id, course, info_data)
                        saved_folders.append(course)

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