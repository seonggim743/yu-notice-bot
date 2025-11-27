import logging
from typing import List, Dict, Any, Optional
from supabase import Client

logger = logging.getLogger(__name__)

class NotificationManager:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    def register_user(self, user_id: int, username: str = None, first_name: str = None):
        """Registers a new user or updates existing one."""
        if not self.supabase: return
        try:
            data = {
                'id': user_id,
                'username': username,
                'first_name': first_name
            }
            self.supabase.table('users').upsert(data).execute()
            logger.info(f"User registered: {user_id}")
        except Exception as e:
            logger.error(f"Failed to register user {user_id}: {e}")

    def add_subscription(self, user_id: int, sub_type: str, value: str):
        """Adds a subscription for a user."""
        if not self.supabase: return
        try:
            data = {
                'user_id': user_id,
                'type': sub_type,
                'value': value
            }
            self.supabase.table('subscriptions').upsert(data, on_conflict='user_id,type,value').execute()
            logger.info(f"Subscription added for {user_id}: {sub_type}={value}")
        except Exception as e:
            logger.error(f"Failed to add subscription for {user_id}: {e}")

    def remove_subscription(self, user_id: int, sub_type: str, value: str):
        """Removes a subscription."""
        if not self.supabase: return
        try:
            self.supabase.table('subscriptions').delete().eq('user_id', user_id).eq('type', sub_type).eq('value', value).execute()
            logger.info(f"Subscription removed for {user_id}: {sub_type}={value}")
        except Exception as e:
            logger.error(f"Failed to remove subscription for {user_id}: {e}")

    def get_subscriptions(self, user_id: int) -> List[Dict]:
        """Gets all subscriptions for a user."""
        if not self.supabase: return []
        try:
            response = self.supabase.table('subscriptions').select('*').eq('user_id', user_id).execute()
            return response.data
        except Exception as e:
            logger.error(f"Failed to get subscriptions for {user_id}: {e}")
            return []

    def check_matches(self, notice: Dict[str, Any]) -> List[int]:
        """
        Checks which users should be notified about this notice.
        Returns a list of user_ids.
        """
        if not self.supabase: return []
        
        matched_user_ids = set()
        
        try:
            # 1. Check Keyword Subscriptions
            # We fetch ALL keyword subscriptions (optimization needed for scale, but fine for now)
            # Or better: Search for subscriptions where value is in title?
            # Supabase doesn't support "value IN string" easily in reverse.
            # So we fetch all keyword subs and filter in Python, OR we rely on a different approach.
            # For now, let's fetch all subscriptions of type 'keyword'.
            
            # Optimization: If we have many users, this is bad.
            # Better approach: Inverted Index or just simple loop if users < 1000.
            # Let's assume small scale for now.
            
            subs_response = self.supabase.table('subscriptions').select('*').execute()
            all_subs = subs_response.data
            
            for sub in all_subs:
                if sub['type'] == 'keyword' and sub['value'] in notice['title']:
                    matched_user_ids.add(sub['user_id'])
                
                elif sub['type'] == 'category' and sub['value'] == notice['category']:
                    matched_user_ids.add(sub['user_id'])
                    
                elif sub['type'] == 'dept' and notice.get('target_dept') and sub['value'] in notice['target_dept']:
                    matched_user_ids.add(sub['user_id'])

        except Exception as e:
            logger.error(f"Failed to check matches: {e}")
            
        return list(matched_user_ids)
