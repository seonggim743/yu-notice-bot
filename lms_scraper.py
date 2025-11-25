import os
import json
import requests
import asyncio
import aiohttp
import urllib.parse
from datetime import datetime, timedelta
from dotenv import load_dotenv
import google.generativeai as genai
import logging
import pytz

from supabase import create_client
from telegram_client import send_telegram

# --- Logging Configuration ---
# KST Timezone
KST = pytz.timezone('Asia/Seoul')

class KSTFormatter(logging.Formatter):
    def converter(self, timestamp):
        dt = datetime.fromtimestamp(timestamp)
        return dt.astimezone(KST)

    def formatTime(self, record, datefmt=None):
        dt = self.converter(record.created)
        if datefmt: return dt.strftime(datefmt)
        return dt.isoformat(timespec='milliseconds')

handlers = [logging.StreamHandler()]
if not os.environ.get('GITHUB_ACTIONS'):
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler('lms_bot.log', maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(KSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))
    handlers.append(file_handler)

for h in handlers:
    h.setFormatter(KSTFormatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(level=logging.INFO, handlers=handlers)
logger = logging.getLogger(__name__)

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
    logger.error("❌ Error: Missing tokens in .env file.")
    logger.error("Ensure CANVAS_TOKEN, TELEGRAM_TOKEN, and CHAT_ID are set.")
    exit(1)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

headers = {
    "Authorization": f"Bearer {CANVAS_TOKEN}"
}

async def send_error_report(error: Exception):
    """Sends a critical error report to the developer/admin."""
    import traceback
    
    tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if len(tb_str) > 3000: tb_str = tb_str[-3000:]
    
    # Escape HTML special characters to prevent Telegram 400 Bad Request
    safe_tb_str = html.escape(tb_str)
    
    msg = (
        f"🚨 <b>LMS BOT CRITICAL ERROR</b>\n\n"
        f"<b>Type:</b> {type(error).__name__}\n"
        f"<b>Message:</b> {html.escape(str(error))}\n\n"
        f"<pre>{safe_tb_str}</pre>"
    )
    
    async with aiohttp.ClientSession() as session:
        # Send to General Topic (ID 1) or fallback
        await send_telegram(session, msg, topic_id=1)

def init_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("⚠️ Supabase credentials missing. State will not be saved.")
        return None
    return create_client(SUPABASE_URL, SUPABASE_KEY)

def load_state(supabase):
    default_state = {
        "notified_assignments": [], 
        "notified_announcements": [],
        "notified_grades": {}, # Changed to dict for {assignment_id: grade}
        "notified_files": []
    }
    if not supabase:
        return default_state
    
    try:
        response = supabase.table('crawling_logs').select('last_post_id').eq('site_name', 'lms_state').execute()
        if response.data:
            state = response.data[0]['last_post_id']
            # Migration: Ensure notified_grades is a dict
            if isinstance(state.get('notified_grades'), list):
                state['notified_grades'] = {}
            return state
    except Exception as e:
        logger.error(f"⚠️ Failed to load state from Supabase: {e}")
    
    return default_state

# ... (save_state remains same)

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
        logger.error(f"❌ Failed to fetch courses: {e}")
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
        logger.error(f"⚠️ Failed to upsert assignment: {e}")

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
        logger.error(f"⚠️ Failed to upsert notice: {e}")

def check_todo(state):
    """Check for To-Do items (Daily Briefing)."""
    # Run only once per day
    today = datetime.now(KST).strftime('%Y-%m-%d')
    if state.get('last_todo_date') == today:
        return []

    logger.info("✅ Checking To-Do List...")
    alerts = []
    
    url = f"{BASE_URL}/users/self/todo"
    try:
        response = requests.get(url, headers=headers)
        todos = response.json()
        
        if not todos:
            return []

        # Filter and format
        todo_items = []
        for item in todos:
            # Item can be assignment or quiz
            if item.get('type') == 'grading': continue # Skip grading tasks (for teachers)
            
            assignment = item.get('assignment')
            quiz = item.get('quiz')
            
            title = "Unknown Task"
            link = ""
            due_str = ""
            
            if assignment:
                title = assignment.get('name')
                link = assignment.get('html_url')
                due_at = assignment.get('due_at')
            elif quiz:
                title = quiz.get('title')
                link = quiz.get('html_url')
                due_at = quiz.get('due_at')
            else:
                continue

            if due_at:
                dt = datetime.strptime(due_at, "%Y-%m-%dT%H:%M:%SZ")
                dt_kst = dt + timedelta(hours=9)
                due_str = dt_kst.strftime('%H:%M')
            
            todo_items.append(f"- <a href='{link}'>{title}</a> (~{due_str})")

        if todo_items:
            msg = (
                f"✅ <b>오늘의 할 일 ({today})</b>\n\n" + 
                "\n".join(todo_items)
            )
            alerts.append({"text": msg})
            state['last_todo_date'] = today

    except Exception as e:
        logger.error(f"⚠️ Error checking todo list: {e}")
        
    return alerts

def check_grades(courses, state):
    """Check for grade updates."""
    logger.info("💯 Checking Grades...")
    alerts = []
    
    for course in courses:
        url = f"{BASE_URL}/courses/{course['id']}/enrollments"
        params = {"type[]": "StudentEnrollment"}
        try:
            response = requests.get(url, headers=headers, params=params)
            enrollments = response.json()
            
            for enrollment in enrollments:
                grades = enrollment.get('grades')
                if not grades: continue
                
                # We can't track individual assignment grades easily via this endpoint alone without fetching all assignments.
                # BUT, we can check 'current_score' or 'current_grade' for the COURSE.
                # To check individual assignment grades, we need to fetch submissions.
                # Let's use the 'submissions' endpoint for recent grading activity.
                pass

        except Exception as e:
            logger.error(f"⚠️ Error checking grades for {course['name']}: {e}")
            
    # Better approach: Check recent submissions with grades
    # GET /api/v1/courses/:course_id/students/submissions?student_ids[]=self&include[]=submission_history
    # Or simply /api/v1/courses/:course_id/assignments/:assignment_id/submissions/:user_id
    
    # Let's use the course-level submissions endpoint which is more efficient
    for course in courses:
        url = f"{BASE_URL}/courses/{course['id']}/students/submissions"
        params = {
            "student_ids[]": "self",
            "order": "graded_at",
            "order_direction": "descending",
            "per_page": 10
        }
        try:
            response = requests.get(url, headers=headers, params=params)
            submissions = response.json()
            
            for sub in submissions:
                if sub.get('workflow_state') != 'graded': continue
                
                assign_id = str(sub.get('assignment_id'))
                current_grade = str(sub.get('grade'))
                current_score = str(sub.get('score'))
                
                # Unique key for state
                state_key = assign_id
                
                last_grade = state['notified_grades'].get(state_key)
                
                if last_grade != current_grade:
                    # Fetch assignment details for name
                    assign_name = "Unknown Assignment"
                    try:
                        a_url = f"{BASE_URL}/courses/{course['id']}/assignments/{assign_id}"
                        a_resp = requests.get(a_url, headers=headers)
                        assign_name = a_resp.json().get('name', assign_name)
                    except: pass

                    msg = (
                        f"💯 <b>[성적] {course['name']}</b>\n"
                        f"📝 <b>{assign_name}</b>\n"
                        f"점수: {current_score}점 ({current_grade})"
                    )
                    alerts.append({"text": msg})
                    state['notified_grades'][state_key] = current_grade

        except Exception as e:
            logger.error(f"⚠️ Error checking submissions for {course['name']}: {e}")
            
    return alerts

def check_files(courses, state):
    """Check for new files."""
    logger.info("📂 Checking Files...")
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
            
            for file in files:
                file_id = str(file['id'])
                if file_id in state['notified_files']:
                    continue
                
                # Check if file is recent (e.g. within last 24 hours) to avoid spamming old files on first run
                # But state should handle it. If state is empty, maybe we skip or just notify latest?
                # Let's notify if it's not in state.
                
                msg = (
                    f"📂 <b>[자료] {course['name']}</b>\n"
                    f"<a href='{file['url']}'><b>{file['display_name']}</b></a>\n"
                    f"등록일: {file['created_at'][:10]}"
                )
                alerts.append({"text": msg})
                state['notified_files'].append(file_id)

        except Exception as e:
            logger.error(f"⚠️ Error checking files for {course['name']}: {e}")
            
    return alerts

def check_assignments(courses, state, supabase=None):
    """Check for upcoming assignments."""
    logger.info("📝 Checking Assignments...")
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
                        f"📝 <a href='{asm['html_url']}'><b>{asm['name']}</b></a>\n"
                        f"⏰ 마감: {due_dt_local.strftime('%m/%d %H:%M')}"
                    )
                    
                    cal_url = generate_calendar_url(f"[과제] {asm['name']}", asm['due_at'])
                    buttons = [{"text": "📅 캘린더 등록", "url": cal_url}] if cal_url else None
                    alerts.append({"text": msg_text, "buttons": buttons})
                    state['notified_assignments'].append(alert_key)

        except Exception as e:
            logger.error(f"⚠️ Error checking assignments for {course['name']}: {e}")
    return alerts

def check_announcements(courses, state, supabase=None):
    """Check for new announcements."""
    logger.info("📢 Checking Announcements...")
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
                f"📢 <b>[공지] {course['name']}</b>\n"
                f"<a href='{ann['html_url']}'><b>{ann['title']}</b></a>\n"
                f"작성자: {ann['user_name']}\n"
                f"작성일: {ann['posted_at'][:10]}"
            )
            if summary:
                msg += f"\n\n📝 <b>요약</b>\n{summary}"
            
            alerts.append({"text": msg})
            state['notified_announcements'].append(str(ann['id']))
            
    except Exception as e:
        logger.error(f"⚠️ Error checking announcements: {e}")
    return alerts

async def main():
    try:
        logger.info(f"🚀 LMS Scraper Started")
        
        supabase = init_supabase()
        state = load_state(supabase)
        courses = get_courses()
        
        if not courses:
            logger.warning("No active courses found.")
            return

        async with aiohttp.ClientSession() as session:
            # 1. Assignments
            assignment_msgs = check_assignments(courses, state, supabase)
            for msg in assignment_msgs:
                await send_telegram(session, msg["text"], buttons=msg.get("buttons"))
                
            # 2. Announcements
            announcement_msgs = check_announcements(courses, state, supabase)
            for msg in announcement_msgs:
                await send_telegram(session, msg["text"])

            # 3. Grades (New)
            grade_msgs = check_grades(courses, state)
            for msg in grade_msgs:
                await send_telegram(session, msg["text"])

            # 4. Files (New)
            file_msgs = check_files(courses, state)
            for msg in file_msgs:
                await send_telegram(session, msg["text"])
            
        save_state(supabase, state)
        logger.info("✅ Done.")
        
    except Exception as e:
        logger.critical(f"🔥 FATAL ERROR: {e}")
        await send_error_report(e)
        raise

if __name__ == "__main__":
    asyncio.run(main())
