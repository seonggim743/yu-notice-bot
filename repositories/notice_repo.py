from typing import Any, Dict, List, Optional, Tuple
from supabase import Client
from models.notice import Notice
from core.database import Database
from core.exceptions import DatabaseException
from core.logger import get_logger
import json

logger = get_logger(__name__)


class NoticeRepository:
    """
    Repository for notice CRUD operations.
    
    Supports dependency injection for testability.
    
    Usage:
        # With DI (recommended)
        db_client = DatabaseClient()
        db = db_client.connect()
        repo = NoticeRepository(db=db)
        
        # Without DI (backward compatible)
        repo = NoticeRepository()
    """
    
    def __init__(self, db: Optional[Client] = None):
        """
        Initialize NoticeRepository.
        
        Args:
            db: Optional Supabase client. If not provided, uses Database.get_client()
        """
        self.db: Client = db or Database.get_client()

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
            raise DatabaseException(
                "Failed to fetch processed notice IDs",
                {"site_key": site_key, "error": str(e)},
            ) from e

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
                .limit(1)
                .execute()
            )
            if not response.data:
                return None

            data = response.data[0]

            # Fix: Parse embedding if it's a string (pgvector/supabase quirk)
            if isinstance(data.get("embedding"), str):
                try:
                    data["embedding"] = json.loads(data["embedding"])
                except json.JSONDecodeError:
                    # pgvector may return malformed JSON, default to empty
                    data["embedding"] = []

            # Fix: Parse message_ids if it's a string
            if isinstance(data.get("message_ids"), str):
                try:
                    data["message_ids"] = json.loads(data["message_ids"])
                except json.JSONDecodeError:
                    # supabase may return malformed JSON, default to empty
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
            raise DatabaseException(
                "Failed to fetch notice",
                {"site_key": site_key, "article_id": article_id, "error": str(e)},
            ) from e

    @staticmethod
    def _serialize_notice(notice: Notice) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        notice_data = notice.model_dump(
            mode="json", exclude={"attachments", "change_details"}
        )
        if "embedding" in notice_data and not notice_data["embedding"]:
            notice_data["embedding"] = None

        attachments_data = [
            {
                "name": attachment.name,
                "url": attachment.url,
                "file_size": attachment.file_size,
                "etag": attachment.etag,
            }
            for attachment in notice.attachments
        ]
        return notice_data, attachments_data

    def upsert_notice(self, notice: Notice) -> Optional[str]:
        """
        Upserts a notice and its attachments using RPC for atomicity.
        Returns the UUID of the inserted/updated record.
        """
        try:
            notice_data, attachments_data = self._serialize_notice(notice)

            # 3. Call RPC
            response = (
                self.db.rpc(
                    "upsert_notice_with_attachments",
                    {"p_notice": notice_data, "p_attachments": attachments_data},
                )
                .execute()
            )

            if not response.data:
                raise RuntimeError("notice upsert RPC returned no notice ID")

            return response.data

        except Exception as e:
            logger.error(f"Failed to upsert notice {notice.title}: {e}")
            raise DatabaseException(
                "Failed to persist notice",
                {"site_key": notice.site_key, "article_id": notice.article_id, "error": str(e)},
            ) from e

    def persist_notice_with_deliveries(
        self, notice: Notice, deliveries: List[Dict[str, Any]]
    ) -> str:
        """Atomically persist a notice version and its channel outbox rows."""
        try:
            notice_data, attachments_data = self._serialize_notice(notice)
            response = self.db.rpc(
                "persist_notice_with_deliveries",
                {
                    "p_notice": notice_data,
                    "p_attachments": attachments_data,
                    "p_deliveries": deliveries,
                },
            ).execute()
            if not response.data:
                raise RuntimeError("notice delivery RPC returned no notice ID")
            return response.data
        except Exception as e:
            logger.error(
                f"Failed to persist notice deliveries for {notice.title}: {e}"
            )
            raise DatabaseException(
                "Failed to persist notice with notification deliveries",
                {"site_key": notice.site_key, "article_id": notice.article_id, "error": str(e)},
            ) from e
