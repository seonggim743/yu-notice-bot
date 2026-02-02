
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import Database
from core.logger import get_logger

logger = get_logger(__name__)

def migrate():
    db = Database.get_client()
    
    sql = """
    CREATE TABLE IF NOT EXISTS ai_models (
        model_name VARCHAR PRIMARY KEY,
        api_key_alias VARCHAR DEFAULT 'default',
        priority INT DEFAULT 99,
        is_active BOOLEAN DEFAULT TRUE,
        blocked_until TIMESTAMP WITH TIME ZONE DEFAULT NULL
    );

    DELETE FROM ai_models; -- Clear old config if any

    INSERT INTO ai_models (model_name, priority) VALUES
    ('gemini-3-flash-preview', 1),
    ('gemini-2.5-flash', 2),
    ('gemini-2.5-pro', 3),
    ('gemini-flash-lite-latest', 4),
    ('gemini-2.5-flash-lite', 5);
    """
    
    print("Please execute the following SQL in your Supabase SQL Editor:")
    print(sql)

if __name__ == "__main__":
    migrate()
