from __future__ import annotations

import logging
import logging_config
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from database_manager import DatabaseManager
from scrapers.base import ListingCandidate, MarketplaceScraper
from scrapers.olx_scraper import OlxScraper
from utils import BASE_DIR

try:  # pragma: no cover - optional dependency
    from scrapers.vinted_scraper import VintedScraper  # type: ignore
except ImportError:
    VintedScraper = None


TARGET_URLS_MESSAGE = (
    "The file 'target_urls.txt' has been created. Add in it at least one URL "
    "to monitor for new ads. Add 1 URL per line."
)
# Store target_urls.txt in the project root (one level above src) so it
# remains easy to edit even after moving the source files.
TARGET_URLS_PATH = os.path.join(BASE_DIR, "../target_urls.txt")
TARGET_OPTIONS_DELIMITER = "||"


@dataclass(frozen=True)
class TargetSpec:
    url: str
    options: Dict[str, str] = field(default_factory=dict)


class ScraperOrchestrator:
    """
    Coordinates multiple scrapers and hides the orchestration logic from the main script.
    """

    def __init__(
        self,
        scrapers: List[MarketplaceScraper] | None = None,
        database: DatabaseManager | None = None,
        target_urls_path: str | None = None,
        listing_workers: int = 4,
        detail_workers: int = 10
    ) -> None:
        self.database = database or DatabaseManager()
        self.target_urls_path = target_urls_path or TARGET_URLS_PATH
        self.listing_workers = max(1, listing_workers)
        self.detail_workers = max(1, detail_workers)
        self.user_agent = self._generate_user_agent()
        if scrapers is not None:
            self.scrapers = scrapers
        else:
            default_scrapers: List[MarketplaceScraper] = [OlxScraper(user_agent=self.user_agent)]
            if VintedScraper is not None:
                try:
                    default_scrapers.append(VintedScraper(user_agent=self.user_agent))
                except Exception as exc:
                    logging.warning(f"Failed to initialize Vinted scraper: {exc}")
            self.scrapers = default_scrapers

    def register_scraper(self, scraper: MarketplaceScraper) -> None:
        """Register an additional marketplace scraper at runtime."""
        self.scrapers.append(scraper)
    def _generate_user_agent(self) -> str:
        base = "Mozilla/5.0"
        platform = random.choice(
            [
                "(Windows NT 10.0; Win64; x64)",
                "(Macintosh; Intel Mac OS X 10_15_7)",
                "(X11; Linux x86_64)",
            ]
        )
        engine = "AppleWebKit/537.36 (KHTML, like Gecko)"
        chrome_version = f"Chrome/{random.randint(90, 118)}.0.{random.randint(1000, 5999)}.{random.randint(10, 99)}"
        safari_version = "Safari/537.36"
        ua = f"{base} {platform} {engine} {chrome_version} {safari_version}"
        logging.debug(f"[orchestrator] Generated UA: {ua}")
        return ua

    def collect_new_ads(self, target_urls: List[str] | None = None) -> List[dict[str]]:
        """
        Load targets, scrape each platform in parallel, and return the new ads with full details.
        """
        if target_urls is None:
            target_specs = self._load_target_urls()
        else:
            target_specs = [TargetSpec(url=url.strip()) for url in target_urls if url and url.strip()]

        if not target_specs:
            logging.info("No target URLs configured. Update target_urls.txt to start monitoring.")
            return []

        new_listing_jobs = self._gather_new_listing_jobs(target_specs)
        if not new_listing_jobs:
            logging.info("No new listings detected in this cycle.")
            return []

        logging.info(f"Fetching details for {len(new_listing_jobs)} new listings.")
        ads = self._fetch_ad_details(new_listing_jobs)
        logging.info(f"Collected detailed data for {len(ads)} listings.")
        return ads

    def _load_target_urls(self) -> List[TargetSpec]:
        try:
            with open(self.target_urls_path) as file:
                lines = file.readlines()
        except FileNotFoundError:
            logging.info(TARGET_URLS_MESSAGE)
            open(self.target_urls_path, "w").close()
            return []

        target_specs: List[TargetSpec] = []
        for line in lines:
            spec = self._parse_target_line(line)
            if spec:
                target_specs.append(spec)

        if not target_specs:
            logging.info(TARGET_URLS_MESSAGE)
        return target_specs

    def _parse_target_line(self, line: str) -> TargetSpec | None:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None

        url_part, sep, options_part = stripped.partition(TARGET_OPTIONS_DELIMITER)
        url = url_part.strip()
        if not url:
            logging.warning("Skipping malformed target line (missing URL).")
            return None

        options: Dict[str, str] = {}
        if sep and options_part.strip():
            for pair in options_part.split(","):
                key, _, value = pair.partition("=")
                key = key.strip()
                value = value.strip()
                if not key or not value:
                    continue
                options[key] = value

        return TargetSpec(url=url, options=options)

    def _gather_new_listing_jobs(self, target_specs: List[TargetSpec]) -> List[Tuple[MarketplaceScraper, ListingCandidate]]:
        jobs: List[Tuple[MarketplaceScraper, ListingCandidate]] = []
        seen_urls: set[str] = set()
        worker_count = max(1, min(self.listing_workers, len(target_specs)))

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(self._scrape_target_url, spec): spec for spec in target_specs}
            for future in as_completed(futures):
                target_spec = futures[future]
                target_url = target_spec.url
                try:
                    scraper, candidates = future.result()
                except Exception as exc:
                    logging.error(f"Error scraping {target_url}: {exc}")
                    continue
                if scraper is None or not candidates:
                    logging.debug(f"No candidates returned for {target_url}")
                    continue
                logging.debug(f"[{scraper.name}] {len(candidates)} raw candidates for {target_url}")
                for candidate in candidates:
                    if candidate.url in seen_urls:
                        logging.debug(f"[{scraper.name}] Duplicate candidate ignored: {candidate.url}")
                        continue
                    seen_urls.add(candidate.url)
                    jobs.append((scraper, candidate))
        logging.debug(f"Total unique listing jobs after dedupe: {len(jobs)}")
        return jobs

    def _scrape_target_url(self, target_spec: TargetSpec) -> Tuple[MarketplaceScraper | None, List[ListingCandidate]]:
        target_url = target_spec.url
        options = target_spec.options
        scraper = self._resolve_scraper(target_url)
        if scraper is None:
            logging.warning(f"No scraper available for URL: {target_url}")
            return None, []

        logging.info(f"[{scraper.name}] Collecting listings for {target_url}")
        try:
            candidates = scraper.collect_listings(target_url, options, self.database.url_exists)
        except ValueError as exc:
            logging.error(exc)
            return scraper, []

        if not candidates:
            logging.debug(f"[{scraper.name}] collect_listings returned 0 candidates for {target_url}")
            return scraper, []

        filtered_candidates = [
            candidate for candidate in candidates
            if candidate.url and not self.database.url_exists(candidate.url)
        ]
        logging.debug(
            f"[{scraper.name}] {len(candidates)} candidates, "
            f"{len(filtered_candidates)} new after DB filter for {target_url}"
        )
        logging.info(f"[{scraper.name}] {len(filtered_candidates)} new listings detected for {target_url}")
        return scraper, filtered_candidates

    def _resolve_scraper(self, target_url: str) -> MarketplaceScraper | None:
        domain = urlparse(target_url).netloc.lower()
        for scraper in self.scrapers:
            if scraper.supports(target_url):
                return scraper
        if "vinted" in domain and not any(scraper.name == "vinted" for scraper in self.scrapers):
            logging.warning(
                "Vinted URL detected but no Vinted scraper is registered. "
                "Install pyVinted (pip install pyVinted) or register a custom scraper."
            )
            return None

    def _fetch_ad_details(self, jobs: List[Tuple[MarketplaceScraper, ListingCandidate]]) -> List[dict[str]]:
        ads: List[dict[str]] = []
        pending_jobs: List[Tuple[MarketplaceScraper, str]] = []

        for scraper, candidate in jobs:
            if candidate.data:
                ad = dict(candidate.data)
                ad.setdefault("url", candidate.url)
                ad["platform"] = scraper.name
                ads.append(ad)
            else:
                pending_jobs.append((scraper, candidate.url))

        if not pending_jobs:
            return ads

        worker_count = max(1, min(self.detail_workers, len(pending_jobs)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_meta = {
                executor.submit(scraper.get_ad_data, ad_url): (scraper.name, ad_url)
                for scraper, ad_url in pending_jobs
            }
            for future in as_completed(future_to_meta):
                scraper_name, ad_url = future_to_meta[future]
                try:
                    ad = future.result()
                except Exception as exc:
                    logging.error(f"[{scraper_name}] Failed to fetch {ad_url}: {exc}")
                    continue
                if not ad:
                    continue
                ad.setdefault("url", ad_url)
                ad["platform"] = scraper_name
                ads.append(ad)
        return ads


