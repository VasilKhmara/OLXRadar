"""
Scraper package exporting marketplace implementations.
"""

from .base import ListingCandidate, MarketplaceScraper  # noqa: F401
from .olx_scraper import OlxScraper  # noqa: F401

try:  # pragma: no cover - optional dependency
    from .vinted_scraper import VintedScraper  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover
    VintedScraper = None  # type: ignore

__all__ = [
    "ListingCandidate",
    "MarketplaceScraper",
    "OlxScraper",
    "VintedScraper",
]


