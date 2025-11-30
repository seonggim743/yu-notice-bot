from typing import List, Dict, Optional, Set
from supabase import Client
from models.notice import Notice
from core.database import Database
from core.logger import get_logger
import json

logger = get_logger(__name__)

class NoticeRepository:
    def __init__(self):
        self.db: Client = Database.get_client()

    def get_last_processed_ids(self, site_key: str, limit: int = 1000) -> Dict[str, str]:
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
                self.db.table('notices')
                .select('article_id, content_hash')
                .eq('site_key', site_key)
                .order('created_at', desc=True)
                .limit(limit)
                .execute()
            )
            return {row['article_id']: row['content_hash'] for row in response.data}
        except Exception as e:
            logger.error(f"Failed to fetch last processed IDs for {site_key}: {e}")
            return {}

    def get_notice(self, site_key: str, article_id: str) -> Optional[Notice]:
        """
        Fetches a full notice object.
        """
        try:
            response = self.db.table('notices').select('*').eq('site_key', site_key).eq('article_id', article_id).single().execute()
            if not response.data: return None
            
            data = response.data
            
            # Fix: Parse embedding if it's a string (pgvector/supabase quirk)
            if isinstance(data.get('embedding'), str):
                try:
                    data['embedding'] = json.loads(data['embedding'])
                except:
                    data['embedding'] = []
            
            # Fetch attachments
            att_resp = self.db.table('attachments').select('*').eq('notice_id', data['id']).execute()
            data['attachments'] = att_resp.data
            
            return Notice(**data)
        except Exception as e:
            logger.error(f"Failed to fetch notice {site_key}/{article_id}: {e}")
            return None

    def upsert_notice(self, notice: Notice) -> Optional[str]:
        """
        Upserts a notice and its attachments.
        Returns the UUID of the inserted/updated record.
        """
        try:
            # 1. Upsert Notice
            data = notice.model_dump(exclude={'attachments'})
            # Convert datetime to ISO format if needed (Pydantic usually handles this)
            if data.get('published_at'):
                data['published_at'] = data['published_at'].isoformat()
            
            # Remove None values to let DB defaults work (though we set most fields)
            # data = {k: v for k, v in data.items() if v is not None}
            # UPDATE: We WANT to update fields to None (e.g. image_url) if they are cleared.
            # But we should remove keys that are NOT in the model fields or should be handled by DB defaults if missing?
            # Pydantic model_dump already handles this. We just need to ensure we don't send 'id' if it's auto-generated (Notice model doesn't have id field).
            pass
            
            response = self.db.table('notices').upsert(data, on_conflict='site_key, article_id').execute()
            
            if not response.data:
                logger.error(f"Upsert returned no data for {notice.title}")
                return None
                
            notice_id = response.data[0]['id']
            
            # 2. Handle Attachments (Delete old, Insert new)
            # Efficient way: Delete all for this notice_id and re-insert.
            self.db.table('attachments').delete().eq('notice_id', notice_id).execute()
            
            if notice.attachments:
                att_data = [
                    {'notice_id': notice_id, 'name': a.name, 'url': a.url} 
                    for a in notice.attachments
                ]
                self.db.table('attachments').insert(att_data).execute()
                
            return notice_id
            
        except Exception as e:
            logger.error(f"Failed to upsert notice {notice.title}: {e}")
            return None

    def update_message_ids(self, notice_id: str, platform: str, message_id: str):
        """
        Updates the message_ids JSONB column.
        """
        try:
            # First fetch existing
            resp = self.db.table('notices').select('message_ids').eq('id', notice_id).single().execute()
            current_ids = resp.data.get('message_ids') or {}
            
            current_ids[platform] = message_id
            
            self.db.table('notices').update({'message_ids': current_ids}).eq('id', notice_id).execute()
        except Exception as e:
            logger.error(f"Failed to update message ID for {notice_id}: {e}")

    def update_discord_thread_id(self, notice_id: str, thread_id: str):
        """
        Updates the discord_thread_id column.
        """
        try:
            self.db.table('notices').update({'discord_thread_id': thread_id}).eq('id', notice_id).execute()
        except Exception as e:
            logger.error(f"Failed to update Discord Thread ID for {notice_id}: {e}")
