import asyncio
import os
from core.database import get_db


async def run_migration():
    print("Connecting to database...")
    db = await get_db()

    print("Reading migration file...")
    with open("migrate_schema_v3.sql", "r") as f:
        sql = f.read()

    print("Executing migration...")
    try:
        await db.execute(sql)
        print("Migration applied successfully!")
    except Exception as e:
        print(f"Migration failed: {e}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(run_migration())
