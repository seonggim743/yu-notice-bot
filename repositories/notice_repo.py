from typing import Dict, Optional, Set
from supabase import Client
from models.notice import Notice
from core.database import Database
from core.logger import get_logger
import json

logger = get_logger(__name__)


class NoticeRepository:
    def __init__(self):
        self.db: Client = Database.get_client()

    def get_last_processed_ids(
        self, site_key: str, limit: int = 1000
    ) -> Dict[str, str]:
        """
        Returns a dict of {article_id: content_hash} for a given site.
        Used to quickly filter new/modified posts.

        Args:
            site_key: Site identifier
            limit: Maximum number of records to fetch (default: 1000)

        Returns:
            Dictionary mapping article_id to content_hash
        """
        try:
            # Fetch recent records ordered by created_at
            response = (
                self.db.table("notices")
                .select("article_id, content_hash")
                .eq("site_key", site_key)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return {row["article_id"]: row["content_hash"] for row in response.data}
        except Exception as e:
            logger.error(f"Failed to fetch last processed IDs for {site_key}: {e}")
            return {}

    def get_notice(self, site_key: str, article_id: str) -> Optional[Notice]:
        """
        Fetches a full notice object.
        """
        try:
            response = (
                self.db.table("notices")
                .select("*")
                .eq("site_key", site_key)
                .eq("article_id", article_id)
                .single()
                .execute()
            )
            if not response.data:
                return None

            data = response.data

            # Fix: Parse embedding if it's a string (pgvector/supabase quirk)
            if isinstance(data.get("embedding"), str):
                try:
                    data["embedding"] = json.loads(data["embedding"])
                except:
                    data["embedding"] = []

            # Fix: Parse message_ids if it's a string
            if isinstance(data.get("message_ids"), str):
                try:
                    data["message_ids"] = json.loads(data["message_ids"])
                except:
                    data["message_ids"] = {}

            # Fetch attachments
            att_resp = (
                self.db.table("attachments")
                .select("*")
                .eq("notice_id", data["id"])
                .execute()
            )
            data["attachments"] = att_resp.data

            return Notice(**data)
        except Exception as e:
            logger.error(f"Failed to fetch notice {site_key}/{article_id}: {e}")
            return None

    def upsert_notice(self, notice: Notice) -> Optional[str]:
        """
        Upserts a notice and its attachments using RPC for atomicity.
        Returns the UUID of the inserted/updated record.
        """
        try:
            # 1. Prepare Notice Data
            # Exclude attachments as they are passed separately
            # Exclude change_details as it's not in DB schema
            notice_data = notice.model_dump(exclude={"attachments", "change_details"})

            # Convert datetime to ISO format
            if notice_data.get("published_at"):
                notice_data["published_at"] = notice_data["published_at"].isoformat()

            # 2. Prepare Attachments Data
            attachments_data = []
            if notice.attachments:
                attachments_data = [
                    {
                        "name": a.name,
                        "url": a.url,
                        "file_size": a.file_size,
                        "etag": a.etag,
                    }
                    for a in notice.attachments
                ]

            # 3. Call RPC
            response = (
                self.db.rpc(
                    "upsert_notice_with_attachments",
                    {"p_notice": notice_data, "p_attachments": attachments_data},
                )
                .execute()
            )

            if not response.data:
                # RPC returns UUID directly, so response.data should be the UUID string
                logger.error(f"RPC returned no data for {notice.title}")
                return None

            return response.data

        except Exception as e:
            logger.error(f"Failed to upsert notice {notice.title}: {e}")
            return None

    def update_message_ids(self, notice_id: str, platform: str, message_id: str):
        """
        Updates the message_ids JSONB column.
        """
        try:
            # First fetch existing
            resp = (
                self.db.table("notices")
                .select("message_ids")
                .eq("id", notice_id)
                .single()
                .execute()
            )
            current_ids = resp.data.get("message_ids") or {}

            current_ids[platform] = message_id

            self.db.table("notices").update({"message_ids": current_ids}).eq(
                "id", notice_id
            ).execute()
        except Exception as e:
            logger.error(f"Failed to update message ID for {notice_id}: {e}")

    def update_discord_thread_id(self, notice_id: str, thread_id: str):
        """
        Updates the discord_thread_id column.
        """
        try:
            self.db.table("notices").update({"discord_thread_id": thread_id}).eq(
                "id", notice_id
            ).execute()
        except Exception as e:
            logger.error(f"Failed to update Discord Thread ID for {notice_id}: {e}")
