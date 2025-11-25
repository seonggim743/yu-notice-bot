import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

CANVAS_TOKEN = os.getenv('CANVAS_TOKEN')
BASE_URL = "https://canvas.yu.ac.kr/api/v1"

if not CANVAS_TOKEN:
    print("❌ Error: CANVAS_TOKEN not found in .env file.")
    print("Please create a .env file and add: CANVAS_TOKEN=your_token_here")
    exit(1)

headers = {
    "Authorization": f"Bearer {CANVAS_TOKEN}"
}

def get_courses():
    """Fetch active courses."""
    print("📚 Fetching Courses...")
    url = f"{BASE_URL}/courses"
    params = {
        "enrollment_state": "active",
        "per_page": 50
    }
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        print(f"❌ Failed to fetch courses: {response.status_code} {response.text}")
        return []
    
    courses = response.json()
    # Filter out courses without names or access restricted
    valid_courses = [c for c in courses if c.get('name') and not c.get('access_restricted_by_date')]
    print(f"✅ Found {len(valid_courses)} active courses.")
    return valid_courses

def get_assignments(course_id, course_name):
    """Fetch upcoming assignments for a course."""
    url = f"{BASE_URL}/courses/{course_id}/assignments"
    params = {
        "bucket": "upcoming", # Only upcoming assignments
        "order_by": "due_at"
    }
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        print(f"⚠️ Failed to fetch assignments for {course_name}")
        return []
    
    return response.json()

def main():
    print(f"🔗 Connecting to {BASE_URL}...")
    
    courses = get_courses()
    
    print("\n📝 Checking Assignments...")
    total_assignments = 0
    
    for course in courses:
        course_name = course['name']
        course_id = course['id']
        
        assignments = get_assignments(course_id, course_name)
        
        if assignments:
            print(f"\n📘 {course_name}")
            for asm in assignments:
                title = asm['name']
                due_at = asm.get('due_at')
                
                # Format Date
                due_str = "No Due Date"
                if due_at:
                    dt = datetime.strptime(due_at, "%Y-%m-%dT%H:%M:%SZ")
                    due_str = dt.strftime("%Y-%m-%d %H:%M")
                
                print(f"  - [ ] {title} (~{due_str})")
                total_assignments += 1
    
    if total_assignments == 0:
        print("\n🎉 No upcoming assignments found!")
    else:
        print(f"\n🔥 Total {total_assignments} upcoming assignments.")

if __name__ == "__main__":
    main()
