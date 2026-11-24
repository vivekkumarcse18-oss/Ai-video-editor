import os, json
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.environ.get("WORKSPACE_DIR", SCRIPT_DIR)

def upload_video():
    secrets_file = os.path.join(BASE_DIR, "client_secrets.json")
    if not os.path.exists(secrets_file):
        secrets_file = os.path.join(SCRIPT_DIR, "client_secrets.json")
    flow = InstalledAppFlow.from_client_secrets_file(secrets_file, SCOPES)
    youtube = build("youtube", "v3", credentials=flow.run_local_server(port=0))
    with open(os.path.join(BASE_DIR, "metadata.json"), "r", encoding="utf-8") as f:
        meta = json.load(f)
    body = {
        "snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"], "categoryId": meta.get("categoryId", "27")},
        "status": {"privacyStatus": meta.get("privacyStatus", "private"), "selfDeclaredMadeForKids": False}
    }
    media = MediaFileUpload(os.path.join(BASE_DIR, "Export", "final_video.mp4"), chunksize=-1, resumable=True)
    res = youtube.videos().insert(part="snippet,status", body=body, media_body=media).execute()
    print("Uploaded Video ID:", res["id"])
    thumb = os.path.join(BASE_DIR, "thumbnail.jpg")
    if not os.path.exists(thumb):
        thumb = os.path.join(SCRIPT_DIR, "thumbnail.jpg")
    if os.path.exists(thumb):
        youtube.thumbnails().set(videoId=res["id"], media_body=MediaFileUpload(thumb)).execute()
        print("Thumbnail uploaded!")

if __name__ == "__main__":
    upload_video()
