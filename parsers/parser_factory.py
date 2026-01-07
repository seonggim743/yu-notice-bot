"""
ParserFactory for creating parser instances based on site key.
Implements Factory Pattern for OCP compliance.
"""
from typing import Dict, Type, Optional, Callable

from core.logger import get_logger
from parsers.html_parser import HTMLParser
from parsers.eoullim_parser import EoullimParser
from parsers.yutopia_parser import YutopiaParser

logger = get_logger(__name__)


class ParserFactory:
    """
    Factory for creating parser instances based on site key.
    
    Follows Open-Closed Principle (OCP):
    - Open for extension: New parsers can be registered via register_parser()
    - Closed for modification: No need to modify factory code when adding parsers
    
    Usage:
        factory = ParserFactory()
        parser = factory.get_parser("yu_news", list_sel, title_sel, link_sel, content_sel)
        
        # Register custom parser
        factory.register_parser("custom_site", CustomParser)
    """
    
    # Default parser mappings (prefix -> parser class)
    # Using prefix matching for flexibility (e.g., "eoullim_" matches "eoullim_career")
    _PREFIX_PARSERS: Dict[str, Type] = {
        "eoullim_": EoullimParser,
    }
    
    # Exact match parsers (key -> parser class)
    _EXACT_PARSERS: Dict[str, Type] = {
        "yutopia": YutopiaParser,
    }
    
    # Default parser for unmatched keys
    _DEFAULT_PARSER: Type = HTMLParser
    
    def __init__(self):
        """Initialize with default parser registry."""
        # Instance-level registries for runtime extension
        self._prefix_parsers = dict(self._PREFIX_PARSERS)
        self._exact_parsers = dict(self._EXACT_PARSERS)
        self._default_parser = self._DEFAULT_PARSER
    
    def get_parser(
        self,
        site_key: str,
        list_selector: str,
        title_selector: str,
        link_selector: str,
        content_selector: str,
    ):
        """
        Creates and returns appropriate parser instance for the given site key.
        
        Args:
            site_key: Site identifier (e.g., "yu_news", "eoullim_career", "yutopia")
            list_selector: CSS selector for notice list
            title_selector: CSS selector for notice title
            link_selector: CSS selector for notice link
            content_selector: CSS selector for notice content
            
        Returns:
            Parser instance (HTMLParser, EoullimParser, YutopiaParser, or custom)
        """
        parser_class = self._resolve_parser_class(site_key)
        
        logger.debug(
            f"[PARSER_FACTORY] Creating {parser_class.__name__} for site_key: {site_key}"
        )
        
        return parser_class(
            list_selector,
            title_selector,
            link_selector,
            content_selector
        )
    
    def _resolve_parser_class(self, site_key: str) -> Type:
        """
        Resolves parser class for given site key.
        
        Priority:
        1. Exact match in _exact_parsers
        2. Prefix match in _prefix_parsers
        3. Default parser
        
        Args:
            site_key: Site identifier
            
        Returns:
            Parser class
        """
        # 1. Check exact match
        if site_key in self._exact_parsers:
            return self._exact_parsers[site_key]
        
        # 2. Check prefix match
        for prefix, parser_class in self._prefix_parsers.items():
            if site_key.startswith(prefix):
                return parser_class
        
        # 3. Return default
        return self._default_parser
    
    def register_parser(
        self,
        key_or_prefix: str,
        parser_class: Type,
        is_prefix: bool = False
    ) -> None:
        """
        Registers a new parser for a site key or prefix.
        
        This enables OCP compliance - extend without modifying existing code.
        
        Args:
            key_or_prefix: Site key for exact match, or prefix for prefix match
            parser_class: Parser class to instantiate
            is_prefix: If True, registers as prefix match; otherwise exact match
            
        Example:
            # Register for exact key
            factory.register_parser("custom_site", CustomParser)
            
            # Register for prefix (e.g., "external_" matches "external_news")
            factory.register_parser("external_", ExternalParser, is_prefix=True)
        """
        if is_prefix:
            self._prefix_parsers[key_or_prefix] = parser_class
            logger.info(f"[PARSER_FACTORY] Registered prefix parser: {key_or_prefix} -> {parser_class.__name__}")
        else:
            self._exact_parsers[key_or_prefix] = parser_class
            logger.info(f"[PARSER_FACTORY] Registered exact parser: {key_or_prefix} -> {parser_class.__name__}")
    
    def set_default_parser(self, parser_class: Type) -> None:
        """
        Sets the default parser class for unmatched keys.
        
        Args:
            parser_class: Parser class to use as default
        """
        self._default_parser = parser_class
        logger.info(f"[PARSER_FACTORY] Set default parser: {parser_class.__name__}")
    
    def get_registered_parsers(self) -> Dict[str, str]:
        """
        Returns a dictionary of all registered parsers.
        
        Returns:
            Dict mapping keys/prefixes to parser class names
        """
        result = {}
        
        for key, cls in self._exact_parsers.items():
            result[f"[exact] {key}"] = cls.__name__
        
        for prefix, cls in self._prefix_parsers.items():
            result[f"[prefix] {prefix}"] = cls.__name__
        
        result["[default]"] = self._default_parser.__name__
        
        return result


# Singleton instance for convenience
_parser_factory: Optional[ParserFactory] = None


def get_parser_factory() -> ParserFactory:
    """
    Returns the global ParserFactory singleton.
    
    Returns:
        ParserFactory instance
    """
    global _parser_factory
    if _parser_factory is None:
        _parser_factory = ParserFactory()
    return _parser_factory
