import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def apply_migration():
    # Try to find a database URL
    db_url = os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    
    if not db_url:
        print("❌ Error: Could not find DATABASE_URL or SUPABASE_DB_URL in environment.")
        print("Cannot apply migration automatically. Please run the SQL in 'migrations/002_upsert_notice_rpc.sql' manually.")
        return

    migration_file = "migrations/002_upsert_notice_rpc.sql"
    
    try:
        with open(migration_file, 'r', encoding='utf-8') as f:
            sql = f.read()
            
        print(f"Applying migration: {migration_file}...")
        
        # Connect to database
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        
        try:
            cur.execute(sql)
            print("✅ Migration applied successfully!")
        except Exception as e:
            print(f"❌ Failed to execute SQL: {e}")
        finally:
            cur.close()
            conn.close()
            
    except FileNotFoundError:
        print(f"❌ Error: Migration file {migration_file} not found.")
    except Exception as e:
        print(f"❌ An error occurred: {e}")

if __name__ == "__main__":
    apply_migration()
