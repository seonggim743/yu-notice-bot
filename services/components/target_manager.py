"""
TargetManager component for loading and managing scraping targets.
Extracted from ScraperService for Single Responsibility Principle.
Uses ParserFactory for OCP-compliant parser creation.
"""
import os
import json
from typing import Dict, List, Optional

from core.logger import get_logger
from models.target import Target
from parsers.parser_factory import ParserFactory, get_parser_factory

logger = get_logger(__name__)


class TargetManager:
    """
    Manages scraping target configuration.
    Responsible for loading, validating, and filtering targets.
    
    Uses ParserFactory for creating parser instances, enabling OCP compliance.
    """
    
    # Default path to targets.json (relative to this file)
    DEFAULT_TARGETS_PATH = os.path.join(
        os.path.dirname(__file__), "../../resources/targets.json"
    )
    
    def __init__(
        self,
        targets_path: Optional[str] = None,
        parser_factory: Optional[ParserFactory] = None,
    ):
        """
        Initialize TargetManager.
        
        Args:
            targets_path: Optional custom path to targets.json file.
                         If not provided, uses DEFAULT_TARGETS_PATH.
            parser_factory: Optional ParserFactory instance for DI.
                           If not provided, uses global singleton.
        """
        self.targets_path = targets_path or self.DEFAULT_TARGETS_PATH
        self.parser_factory = parser_factory or get_parser_factory()
        self._targets: List[Dict] = []
        self._all_targets: List[Dict] = []  # Original unfiltered list
    
    def load_targets(self) -> List[Dict]:
        """
        Loads targets from resources/targets.json and validates them.
        Creates appropriate parser instances for each target using ParserFactory.
        
        Returns:
            List of validated target dictionaries with parser instances.
        """
        if not os.path.exists(self.targets_path):
            logger.error(f"[TARGET_MANAGER] Targets file not found at {self.targets_path}")
            return []

        try:
            with open(self.targets_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            valid_targets = []
            for item in data:
                try:
                    target = Target(**item)
                    target_dict = target.model_dump()
                    
                    # Use ParserFactory for OCP-compliant parser creation
                    target_dict["parser"] = self.parser_factory.get_parser(
                        site_key=target.key,
                        list_selector=target.list_selector,
                        title_selector=target.title_selector,
                        link_selector=target.link_selector,
                        content_selector=target.content_selector,
                    )
                    valid_targets.append(target_dict)
                    
                except Exception as e:
                    logger.error(
                        f"[TARGET_MANAGER] Invalid target configuration: "
                        f"{item.get('key', 'unknown')} - {e}"
                    )
            
            logger.info(
                f"[TARGET_MANAGER] Loaded {len(valid_targets)} targets from {self.targets_path}"
            )
            
            self._targets = valid_targets
            self._all_targets = valid_targets.copy()
            return valid_targets
            
        except Exception as e:
            logger.error(f"[TARGET_MANAGER] Failed to load targets: {e}")
            return []
    
    def filter_targets(self, target_key: str) -> None:
        """
        Filters the targets list to only include the specified key.
        
        Args:
            target_key: The key of the target to keep
        """
        original_count = len(self._targets)
        available_keys = [t["key"] for t in self._targets]
        
        self._targets = [t for t in self._targets if t["key"] == target_key]
        
        if not self._targets:
            logger.warning(
                f"[TARGET_MANAGER] Target '{target_key}' not found! "
                f"Available keys: {available_keys}"
            )
        else:
            logger.info(
                f"[TARGET_MANAGER] Filtered targets: {original_count} -> "
                f"{len(self._targets)} (Target: {target_key})"
            )
    
    def get_targets(self) -> List[Dict]:
        """Returns the current list of targets."""
        return self._targets
    
    def get_all_targets(self) -> List[Dict]:
        """Returns the original unfiltered list of targets."""
        return self._all_targets
    
    def get_targets_by_auth_type(self) -> Dict[str, List[Dict]]:
        """
        Groups targets by authentication type.
        
        Returns:
            Dictionary with keys: 'public', 'eoullim', 'yutopia'
        """
        result = {
            "public": [],
            "eoullim": [],
            "yutopia": [],
        }
        
        for target in self._targets:
            key = target["key"]
            if key.startswith("eoullim_"):
                result["eoullim"].append(target)
            elif key == "yutopia":
                result["yutopia"].append(target)
            else:
                result["public"].append(target)
        
        return result
    
    def reset_filter(self) -> None:
        """Resets the target filter to include all targets."""
        self._targets = self._all_targets.copy()
        logger.info(f"[TARGET_MANAGER] Filter reset. {len(self._targets)} targets available.")
