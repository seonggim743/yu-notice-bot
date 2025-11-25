import os
import json
import requests
import asyncio
import urllib.parse
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables
load_dotenv()

from supabase import create_client, Client

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

async def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if "reply_markup" in message:
        payload["reply_markup"] = message["reply_markup"]
        payload["text"] = message["text"]
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"⚠️ Telegram send failed: {e}")

def generate_calendar_url(title, date_str):
    """Generates a Google Calendar Add URL."""
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%dT%H:%M:%SZ')
        dt_kst = dt + timedelta(hours=9)
        
        # Format: YYYYMMDDTHHMMSS
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
        model = genai.GenerativeModel('gemini-2.0-flash-exp')
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
        # Filter valid courses
        return [c for c in courses if c.get('name') and not c.get('access_restricted_by_date')]
    except Exception as e:
        print(f"❌ Failed to fetch courses: {e}")
        return []

def check_assignments(courses, state):
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
                # Skip if submitted
                if asm.get('has_submitted_submissions'):
                    continue
                
                # Skip if no due date
                if not asm.get('due_at'):
                    continue

                due_dt = datetime.strptime(asm['due_at'], "%Y-%m-%dT%H:%M:%SZ")
                # Adjust to KST (Canvas usually returns UTC)
                due_dt_kst = due_dt + timedelta(hours=9)
                now_kst = datetime.now() + timedelta(hours=9) # Local time approximation if machine is UTC
                # Better: just use machine local time if running locally in Korea
                # Assuming local machine is KST:
                due_dt_local = due_dt + timedelta(hours=9) # UTC to KST
                now_local = datetime.now()
                
                time_diff = due_dt_local - now_local
                days_left = time_diff.days
                
                # Alert Logic: 3 days before, 1 day before, or D-Day (less than 24h)
                # We use a unique key to prevent duplicate alerts ON THE SAME DAY
                # Key format: "ID_DATE" e.g. "12345_2023-10-25"
                today_str = now_local.strftime('%Y-%m-%d')
                alert_key = f"{asm['id']}_{today_str}"
                
                if alert_key in state['notified_assignments']:
                    continue

                msg = None
                if 0 <= days_left <= 3:
                    if days_left == 0:
                        msg = f"🚨 <b>[D-Day] 과제 마감 임박!</b>\n"
                    else:
                        msg = f"⏳ <b>[D-{days_left}] 과제 마감 알림</b>\n"
                    
                    msg += (
                        f"📚 {course['name']}\n"
                        f"📝 <a href='{asm['html_url']}'>{asm['name']}</a>\n"
                        f"⏰ 마감: {due_dt_local.strftime('%m/%d %H:%M')}"
                    )
                    
                    cal_url = generate_calendar_url(f"[과제] {asm['name']}", asm['due_at'])
                    msg = {
                        "text": msg_text,
                        "reply_markup": json.dumps({"inline_keyboard": [[{"text": "📅 캘린더 등록", "url": cal_url}]]}) if cal_url else None
                    }
                    alerts.append(msg)
                    state['notified_assignments'].append(alert_key)

        except Exception as e:
            print(f"⚠️ Error checking assignments for {course['name']}: {e}")

    return alerts

def check_announcements(courses, state):
    """Check for new announcements."""
    print("📢 Checking Announcements...")
    alerts = []
    
    # Canvas Announcements API is context_codes based
    # /api/v1/announcements?context_codes[]=course_123&context_codes[]=course_456
    context_codes = [f"course_{c['id']}" for c in courses]
    
    # Split into chunks if too many courses (limit is usually safe though)
    url = f"{BASE_URL}/announcements"
    params = {
        "context_codes[]": context_codes,
        "start_date": (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"), # Check last 7 days
        "active_only": True
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        announcements = response.json()
        
        for ann in announcements:
            if str(ann['id']) in state['notified_announcements']:
                continue
            
            # Find course name
            course_id = int(ann['context_code'].split('_')[1])
            course_name = next((c['name'] for c in courses if c['id'] == course_id), "Unknown Course")
            
            msg = (
                f"📢 <b>[공지] {course_name}</b>\n"
                f"<a href='{ann['html_url']}'>{ann['title']}</a>\n"
                f"작성자: {ann['user_name']}\n"
                f"작성일: {ann['posted_at'][:10]}"
            )
            # AI Summary
            summary = get_ai_summary(ann['message'])
            if summary:
                msg += f"\n\n🤖 <b>AI 요약</b>\n{summary}"

            alerts.append({"text": msg})
            state['notified_announcements'].append(str(ann['id']))
            
    except Exception as e:
        print(f"⚠️ Error checking announcements: {e}")
        
    return alerts

def check_grades(courses, state):
    """Check for new grades."""
    print("💯 Checking Grades...")
    alerts = []
    
    for course in courses:
        # Check submissions for graded items
        # /api/v1/courses/:course_id/students/submissions
        url = f"{BASE_URL}/courses/{course['id']}/students/submissions"
        params = {
            "student_ids[]": "self",
            "grouped": True,
            "include[]": "assignment"
        }
        try:
            response = requests.get(url, headers=headers, params=params)
            submissions = response.json()
            
            if not submissions or not isinstance(submissions, list): continue
            
            # submissions is a list of student objects if grouped=True, but with student_ids=self it might be direct list
            # Actually with student_ids=self, it returns a list of submissions directly if not grouped?
            # Let's handle list of submissions.
            
            # If grouped=True, response is [{user_id:..., submissions:[...]}]
            # If grouped=False, response is [submission, ...]
            
            # Let's try grouped=False for simplicity
            params['grouped'] = False
            response = requests.get(url, headers=headers, params=params)
            submissions = response.json()

            for sub in submissions:
                if sub.get('grade') and sub.get('graded_at'):
                    # Unique key: submission_id + grade (to detect changes)
                    # But submission_id is unique per assignment/user.
                    # If grade changes, we want to know.
                    grade_key = f"{sub['id']}_{sub['grade']}"
                    
                    # Also check if we already notified this submission ID at all (for initial grade)
                    # If we want to notify on CHANGE, we need to store the last grade.
                    # For simplicity, let's just notify if this specific grade_key hasn't been seen.
                    # This implies if grade changes from A to B, key changes, so we notify.
                    
                    if grade_key in state.get('notified_grades', []):
                        continue
                        
                    # Check if it's recent (e.g. graded in last 24h) to avoid spamming old grades on first run?
                    # Or just rely on state.
                    
                    assignment_name = sub.get('assignment', {}).get('name', 'Unknown Assignment')
                    score = sub.get('score')
                    grade = sub.get('grade')
                    
                    msg = (
                        f"💯 <b>[성적 알림] {course['name']}</b>\n"
                        f"📝 {assignment_name}\n"
                        f"점수: {score} (등급: {grade})"
                    )
                    alerts.append({"text": msg})
                    if 'notified_grades' not in state: state['notified_grades'] = []
                    state['notified_grades'].append(grade_key)

        except Exception as e:
            print(f"⚠️ Error checking grades for {course['name']}: {e}")
            
    return alerts

def check_files(courses, state):
    """Check for new files."""
    print("📂 Checking Files...")
    alerts = []
    
    for course in courses:
        url = f"{BASE_URL}/courses/{course['id']}/files"
        params = {
            "sort": "created_at",
            "order": "desc",
            "per_page": 10
        }
        try:
            response = requests.get(url, headers=headers, params=params)
            files = response.json()
            
            for f in files:
                # Check if created recently (last 24h)
                created_at = datetime.strptime(f['created_at'], "%Y-%m-%dT%H:%M:%SZ")
                if datetime.now() - created_at > timedelta(days=1):
                    continue
                
                if str(f['id']) in state.get('notified_files', []):
                    continue
                
                msg = (
                    f"📂 <b>[자료 업로드] {course['name']}</b>\n"
                    f"📄 <a href='{f['url']}'>{f['display_name']}</a>\n"
                    f"크기: {f['size'] // 1024}KB"
                )
                alerts.append({"text": msg})
                if 'notified_files' not in state: state['notified_files'] = []
                state['notified_files'].append(str(f['id']))
                
        except Exception as e:
            print(f"⚠️ Error checking files for {course['name']}: {e}")
            
    return alerts

async def main():
    print(f"🚀 LMS Scraper Started at {datetime.now()}")
    
    supabase = init_supabase()
    state = load_state(supabase)
    courses = get_courses()
    
    if not courses:
        print("No active courses found.")
        return

    # 1. Assignments
    assignment_msgs = check_assignments(courses, state)
    for msg in assignment_msgs:
        await send_telegram(msg)
        
    # 2. Announcements
    announcement_msgs = check_announcements(courses, state)
    for msg in announcement_msgs:
        await send_telegram(msg)
        
    # 3. Grades
    grade_msgs = check_grades(courses, state)
    for msg in grade_msgs:
        await send_telegram(msg)

    # 4. Files
    file_msgs = check_files(courses, state)
    for msg in file_msgs:
        await send_telegram(msg)
        
    save_state(supabase, state)
    print("✅ Done.")

if __name__ == "__main__":
    asyncio.run(main())
