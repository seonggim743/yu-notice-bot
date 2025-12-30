
import sys
import os

sys.path.append(os.getcwd())

try:
    print("Checking scraper_service.py syntax...")
    from services import scraper_service
    print("SUCCESS: scraper_service imported correctly.")
except ImportError as e:
    print(f"IMPORT ERROR: {e}")
except SyntaxError as e:
    print(f"SYNTAX ERROR: {e}")
except Exception as e:
    print(f"ERROR: {e}")

try:
    print("Checking polaris_service.py syntax...")
    from services import polaris_service
    print("SUCCESS: polaris_service imported correctly.")
except Exception as e:
    print(f"ERROR: {e}")
