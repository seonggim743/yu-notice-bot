import os
import json
import requests
import asyncio
import aiohttp
import urllib.parse
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai

from supabase import create_client
from telegram_client import send_telegram

# Load environment variables
load_dotenv()

CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

BASE_URL = "https://canvas.yu.ac.kr/api/v1"

if not CANVAS_TOKEN or not TELEGRAM_TOKEN or not CHAT_ID:
    print("❌ Error: Missing tokens in .env file.")
    print("Ensure CANVAS_TOKEN, TELEGRAM_TOKEN, and CHAT_ID are set.")
    exit(1)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

headers = {
    "Authorization": f"Bearer {CANVAS_TOKEN}"
}

def init_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ Supabase credentials missing. State will not be saved.")
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def load_state(supabase):
    default_state = {
        "notified_assignments": [], 
        "notified_announcements": [],
        "notified_grades": [],
        "notified_files": []
    }
    if not supabase:
        return default_state
    
    try:
        response = supabase.table('crawling_logs').select('last_post_id').eq('site_name', 'lms_state').execute()
        if response.data:
            return response.data[0]['last_post_id']
    except Exception as e:
        print(f"⚠️ Failed to load state from Supabase: {e}")
    
    return default_state

def save_state(supabase, state):
    if not supabase: return
    try:
        data = {'site_name': 'lms_state', 'last_post_id': state}
        supabase.table('crawling_logs').upsert(data).execute()
    except Exception as e:
        print(f"⚠️ Failed to save state to Supabase: {e}")

def generate_calendar_url(title, date_str):
    """Generates a Google Calendar Add URL."""
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%SZ')
        dt_kst = dt + timedelta(hours=9)
        
        start = dt_kst.strftime('%Y%m%dT%H%M00')
        end = (dt_kst + timedelta(hours=1)).strftime('%Y%m%dT%H%M00')
        
        params = {
            'action': 'TEMPLATE',
            'text': title,
            'dates': f"{start}/{end}",
            'details': 'Added by YU Notice Bot'
        }
        return f"https://www.google.com/calendar/render?{urllib.parse.urlencode(params)}"
    except:
        return ""

def get_ai_summary(text):
    if not GEMINI_API_KEY: return None
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"Summarize this announcement in Korean (3 lines max):\n\n{text[:2000]}"
        response = model.generate_content(prompt)
        return response.text.strip()
    except:
        return None

def get_courses():
    """Fetch active courses."""
    url = f"{BASE_URL}/courses"
    params = {"enrollment_state": "active", "per_page": 50}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        courses = response.json()
        return [c for c in courses if c.get('name') and not c.get('access_restricted_by_date')]
    except Exception as e:
        print(f"❌ Failed to fetch courses: {e}")
        return []

def upsert_assignment(supabase, asm, course_name):
    if not supabase: return
    try:
        data = {
            'assignment_id': str(asm['id']),
            'course_name': course_name,
            'title': asm['name'],
            'url': asm['html_url'],
            'due_at': asm['due_at'],
            'is_submitted': asm.get('has_submitted_submissions', False)
        }
        supabase.table('lms_assignments').upsert(data, on_conflict='assignment_id').execute()
    except Exception as e:
        print(f"⚠️ Failed to upsert assignment: {e}")

def upsert_notice(supabase, ann, course_name, summary):
    if not supabase: return
    try:
        data = {
            'notice_id': str(ann['id']),
            'course_name': course_name,
            'title': ann['title'],
            'url': ann['html_url'],
            'content': summary,
            'author': ann['user_name'],
            'posted_at': ann['posted_at']
        }
        supabase.table('lms_notices').upsert(data, on_conflict='notice_id').execute()
    except Exception as e:
        print(f"⚠️ Failed to upsert notice: {e}")

def check_assignments(courses, state, supabase=None):
    """Check for upcoming assignments."""
    print("📝 Checking Assignments...")
    alerts = []
    
    for course in courses:
        url = f"{BASE_URL}/courses/{course['id']}/assignments"
        params = {"bucket": "upcoming", "order_by": "due_at"}
        try:
            response = requests.get(url, headers=headers, params=params)
            assignments = response.json()
            
            for asm in assignments:
                if supabase:
                    upsert_assignment(supabase, asm, course['name'])
                if asm.get('has_submitted_submissions') or not asm.get('due_at'):
                    continue

                due_dt = datetime.strptime(asm['due_at'], "%Y-%m-%dT%H:%M:%SZ")
                due_dt_local = due_dt + timedelta(hours=9)
                now_local = datetime.now()
                
                time_diff = due_dt_local - now_local
                days_left = time_diff.days
                
                today_str = now_local.strftime('%Y-%m-%d')
                alert_key = f"{asm['id']}_{today_str}"
                
                if alert_key in state['notified_assignments']:
                    continue

                if 0 <= days_left <= 3:
                    prefix = f"🚨 <b>[D-Day] 과제 마감 임박!</b>\n" if days_left == 0 else f"⏳ <b>[D-{days_left}] 과제 마감 알림</b>\n"
                    
                    msg_text = (
                        f"{prefix}"
                        f"📚 {course['name']}\n"
                        f"📝 <a href='{asm['html_url']}'>{asm['name']}</a>\n"
                        f"⏰ 마감: {due_dt_local.strftime('%m/%d %H:%M')}"
                    )
                    
                    cal_url = generate_calendar_url(f"[과제] {asm['name']}", asm['due_at'])
                    buttons = [{"text": "📅 캘린더 등록", "url": cal_url}] if cal_url else None
                    alerts.append({"text": msg_text, "buttons": buttons})
                    state['notified_assignments'].append(alert_key)

        except Exception as e:
            print(f"⚠️ Error checking assignments for {course['name']}: {e}")
    return alerts

def check_announcements(courses, state, supabase=None):
    """Check for new announcements."""
    print("📢 Checking Announcements...")
    alerts = []
    
    context_codes = [f"course_{c['id']}" for c in courses]
    url = f"{BASE_URL}/announcements"
    params = {
        "context_codes[]": context_codes,
        "start_date": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
        "active_only": True
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        announcements = response.json()
        
        for ann in announcements:
            if str(ann['id']) in state['notified_announcements']:
                continue
            
            course_id = int(ann['context_code'].split('_')[1])
            course_name = next((c['name'] for c in courses if c['id'] == course_id), "Unknown Course")
            
            summary = get_ai_summary(ann['message'])
            if supabase:
                upsert_notice(supabase, ann, course_name, summary or ann['message'])
            
            msg = (
                f"📢 <b>[공지] {course_name}</b>\n"
                f"<a href='{ann['html_url']}'>{ann['title']}</a>\n"
                f"작성자: {ann['user_name']}\n"
                f"작성일: {ann['posted_at'][:10]}"
            )
            if summary:
                msg += f"\n\n🤖 <b>AI 요약</b>\n{summary}"
            
            alerts.append({"text": msg})
            state['notified_announcements'].append(str(ann['id']))
            
    except Exception as e:
        print(f"⚠️ Error checking announcements: {e}")
    return alerts

async def main():
    print(f"🚀 LMS Scraper Started at {datetime.now()}")
    
    supabase = init_supabase()
    state = load_state(supabase)
    courses = get_courses()
    
    if not courses:
        print("No active courses found.")
        return

    async with aiohttp.ClientSession() as session:
        assignment_msgs = check_assignments(courses, state, supabase)
        for msg in assignment_msgs:
            await send_telegram(session, msg["text"], buttons=msg.get("buttons"))
            
        announcement_msgs = check_announcements(courses, state, supabase)
        for msg in announcement_msgs:
            await send_telegram(session, msg["text"])
        
    save_state(supabase, state)
    print("✅ Done.")

if __name__ == "__main__":
    asyncio.run(main())
