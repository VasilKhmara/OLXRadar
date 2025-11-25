from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, List
from urllib.parse import urlparse


@dataclass(frozen=True)
class ListingCandidate:
    """Represents a listing discovered by a scraper."""

    url: str
    data: dict | None = None


class MarketplaceScraper(ABC):
    """Base contract for marketplace scrapers."""

    name: str = "base"
    supported_domains: tuple[str, ...] = tuple()

    def supports(self, target_url: str) -> bool:
        """Return True if the scraper can handle the given URL."""
        domain = urlparse(target_url).netloc.lower()
        return domain in (d.lower() for d in self.supported_domains if d)

    @abstractmethod
    def collect_listings(
        self,
        target_url: str,
        options: Dict[str, str] | None = None,
        is_known: Callable[[str], bool] | None = None
    ) -> List[ListingCandidate]:
        """Return all listings (with optional preloaded data) for the provided target URL."""

    @abstractmethod
    def get_ad_data(self, ad_url: str) -> dict | None:
        """Return the normalized payload for a single listing URL."""

