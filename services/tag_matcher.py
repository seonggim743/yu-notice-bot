from typing import List, Optional
from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

class TagMatcher:
    """
    Converts AI-selected tag names to Discord tag IDs.
    Simplified version - no longer does keyword matching.
    """
    
    @staticmethod
    def get_tag_ids(tag_names: List[str], site_key: str) -> List[str]:
        """
        Convert tag names to Discord tag IDs.
        
        Args:
            tag_names: List of tag names selected by AI (e.g., ["긴급", "장학"])
            site_key: Site identifier (e.g., 'yu_news')
        
        Returns:
            List of Discord tag IDs (max 5)
        """
        tag_map = settings.DISCORD_TAG_MAP.get(site_key, {})
        if not tag_map:
            logger.debug(f"[TAG] No tag map configured for {site_key}")
            return []
        
        tag_ids = []
        
        for tag_name in tag_names[:5]:  # Discord max 5 tags
            # Try exact match first
            if tag_name in tag_map:
                tag_ids.append(tag_map[tag_name])
                logger.info(f"[TAG] Matched '{tag_name}' for {site_key}")
            else:
                # Try case-insensitive and variation matching
                tag_map_lower = {k.lower(): (k, v) for k, v in tag_map.items()}
                tag_name_lower = tag_name.lower()
                
                if tag_name_lower in tag_map_lower:
                    original_name, tag_id = tag_map_lower[tag_name_lower]
                    tag_ids.append(tag_id)
                    logger.info(f"[TAG] Matched '{tag_name}' -> '{original_name}' for {site_key}")
                else:
                    logger.warning(f"[TAG] Tag '{tag_name}' not found in tag_map for {site_key}")
        
        return tag_ids
