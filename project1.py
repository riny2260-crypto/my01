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
                        "token_uri": "https://oauth2.googleapis.com/token"
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

    detected_name = "