from __future__ import annotations

import logging
import logging_config  # noqa: F401
import re
import threading
import time
from typing import Callable, Dict, List
from urllib.parse import parse_qsl, urlparse, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup
from scrapers.base import ListingCandidate, MarketplaceScraper
from utils import get_header


class VintedScraper(MarketplaceScraper):
    """HTML-based scraper for Vinted catalog pages."""

    name = "vinted"
    max_allowed_page_size = 20
    max_allowed_pages = 1

    def __init__(
        self,
        page_size: int = 20,
        max_pages: int = 1,
        user_agent: str | None = None
    ) -> None:
        self.user_agent = user_agent
        self.page_size = min(max(1, page_size), self.max_allowed_page_size)
        self.max_pages = min(max(1, max_pages), self.max_allowed_pages)
        self.schema = "https"
        self._html_session_local = threading.local()
        self._request_lock = threading.Lock()
        self._last_request_time = 0.0

    def supports(self, target_url: str) -> bool:
        domain = urlparse(target_url).netloc.lower()
        return "vinted" in domain

    def collect_listings(
        self,
        target_url: str,
        options: Dict[str, str] | None = None,
        is_known: Callable[[str], bool] | None = None
    ) -> List[ListingCandidate]:
        parsed_target = urlparse(target_url)
        base_netloc = parsed_target.netloc or "www.vinted.com"
        page_size = self._coerce_int_option(options, "page_size", self.page_size, 1, self.max_allowed_page_size)
        max_pages = self._coerce_int_option(options, "max_pages", self.max_pages, 1, self.max_allowed_pages)
        page = 1
        collected: List[ListingCandidate] = []
        seen_urls: set[str] = set()

        logging.info(f"[{self.name}] Starting scrape for {target_url}")
        while page <= max_pages:
            page_url = self._build_page_url(parsed_target, page, page_size)
            logging.debug(f"[{self.name}] Fetching page {page}: {page_url}")
            soup = self._fetch_search_page(page_url)
            if soup is None:
                logging.warning(f"[{self.name}] Failed to load page {page}")
                break
            
            listing_urls = self._extract_listing_urls(soup, base_netloc, self.schema, max_items=page_size)
            logging.debug(f"[{self.name}] Extracted {len(listing_urls)} URLs from page {page} (limited to {page_size})")
            
            if not listing_urls:
                logging.info(f"[{self.name}] No listings found on page {page}.")
                break

            page_contains_known = False
            new_items = 0
            logging.info(f"[{self.name}] Page {page} contains {len(listing_urls)} candidates")
            for url in listing_urls:
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                if is_known and is_known(url):
                    page_contains_known = True
                    logging.debug(f"[{self.name}] Known listing skipped: {url}")
                    continue
                collected.append(ListingCandidate(url=url))
                new_items += 1
                logging.debug(f"[{self.name}] Queued listing: {url}")

            if new_items == 0:
                logging.info(f"[{self.name}] No new listings on page {page}, stopping.")
                break
            if page_contains_known:
                logging.info(f"[{self.name}] Known listing encountered; stopping pagination.")
                break

            page += 1

        logging.info(f"[{self.name}] Finished scraping. Total unique items: {len(collected)}")
        return collected

    def get_ad_data(self, ad_url: str) -> dict | None:
        html_data = self._scrape_item_from_html(ad_url)
        if html_data:
            logging.debug(f"[{self.name}] HTML scrape succeeded for {ad_url}")
        else:
            logging.debug(f"[{self.name}] HTML scrape returned no data for {ad_url}")
        return html_data

    def _build_page_url(self, parsed_target, page: int, page_size: int) -> str:
        query = dict(parse_qsl(parsed_target.query, keep_blank_values=True))
        query["page"] = str(page)
        query["per_page"] = str(page_size)
        encoded = urlencode(query, doseq=True)
        return urlunparse(parsed_target._replace(query=encoded))

    def _extract_listing_urls(self, soup: BeautifulSoup, netloc: str, scheme: str, max_items: int | None = None) -> List[str]:
        urls: List[str] = []
        
        # Try multiple selectors based on Vinted's HTML structure
        selectors = [
            ".feed-grid__item a[href*='/items/']",
            ".feed-grid__item-content a[href*='/items/']",
            "[data-testid*='catalog-item'] a[href*='/items/']",
            "[data-test-id='catalog-item-card'] a[href*='/items/']",
            "a[href*='/items/']",
        ]
        
        seen_hrefs: set[str] = set()
        for selector in selectors:
            for anchor in soup.select(selector):
                if max_items is not None and len(urls) >= max_items:
                    break
                href = anchor.get("href")
                if not href or href in seen_hrefs:
                    continue
                normalized = self._normalize_listing_url(href, netloc, scheme)
                if normalized:
                    urls.append(normalized)
                    seen_hrefs.add(href)
                    logging.debug(f"[{self.name}] Found listing URL via {selector}: {normalized}")
            if urls and (max_items is None or len(urls) >= max_items):
                break  # Found URLs with this selector, no need to try others
        
        logging.debug(f"[{self.name}] Total unique listing URLs extracted: {len(urls)}" + (f" (limited to {max_items})" if max_items else ""))
        return urls

    @staticmethod
    def _normalize_listing_url(href: str | None, netloc: str, scheme: str) -> str | None:
        if not href:
            return None
        # Remove query params and fragments
        href = href.split("?")[0].split("#")[0]
        href = href.strip()
        if not href or "/items/" not in href:
            return None
        if href.startswith("//"):
            return f"{scheme}:{href}"
        if href.startswith("/"):
            return f"{scheme}://{netloc}{href}"
        if href.startswith("http"):
            return href
        return f"{scheme}://{netloc}/{href.lstrip('/')}"

    def _fetch_search_page(self, url: str) -> BeautifulSoup | None:
        # Ensure sequential requests with 1 second delay
        self._wait_for_rate_limit()
        
        session = self._get_html_session()
        delays = (0, 1, 2, 4)
        last_error: Exception | None = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                logging.debug(f"[{self.name}] Sleeping {delay}s before page {attempt} request")
                time.sleep(delay)
            try:
                logging.debug(f"[{self.name}] Fetching search page (attempt {attempt}/{len(delays)}): {url}")
                response = session.get(url, timeout=15)
                logging.debug(f"[{self.name}] Response status: {response.status_code}, size: {len(response.text)} bytes")
            except requests.RequestException as exc:
                last_error = exc
                logging.debug(f"[{self.name}] Search request failed: {exc}")
                continue
            if response.status_code in (403, 429):
                last_error = requests.HTTPError(f"HTTP {response.status_code}")
                logging.warning(
                    f"[{self.name}] Search page {url} returned {response.status_code}. "
                    "Not retrying (blocked/rate limited)."
                )
                return None
            if response.status_code != 200:
                last_error = requests.HTTPError(f"HTTP {response.status_code}")
                logging.warning(
                    f"[{self.name}] Unexpected status {response.status_code} for {url}"
                )
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            logging.debug(f"[{self.name}] Successfully parsed HTML, found {len(soup.find_all('a', href=re.compile('/items/')))} item links")
            return soup
        if last_error:
            logging.error(f"[{self.name}] Exhausted search retries: {last_error}")
        return None

    def _wait_for_rate_limit(self) -> None:
        """Ensure at least 1 second between requests to avoid rate limiting."""
        with self._request_lock:
            current_time = time.time()
            time_since_last = current_time - self._last_request_time
            if time_since_last < 1.0:
                sleep_time = 1.0 - time_since_last
                logging.debug(f"[{self.name}] Rate limit delay: sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
            self._last_request_time = time.time()

    def _fetch_item_soup(self, item_url: str) -> BeautifulSoup | None:
        # Ensure sequential requests with 1 second delay
        self._wait_for_rate_limit()
        
        session = self._get_html_session()
        delays = (0, 1, 2, 4)
        last_error: Exception | None = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                logging.debug(f"[{self.name}] Sleeping {delay}s before HTML retry #{attempt} for {item_url}")
                time.sleep(delay)
            try:
                logging.debug(f"[{self.name}] Fetching item page (attempt {attempt}): {item_url}")
                response = session.get(item_url, timeout=15)
                logging.debug(f"[{self.name}] Item page response: {response.status_code}, {len(response.text)} bytes")
            except requests.RequestException as exc:
                last_error = exc
                logging.debug(f"[{self.name}] HTML fetch exception for {item_url}: {exc}")
                continue

            if response.status_code in (403, 429):
                last_error = requests.HTTPError(f"HTTP {response.status_code}")
                logging.warning(
                    f"[{self.name}] HTML fetch for {item_url} returned {response.status_code}. "
                    "Not retrying (blocked/rate limited)."
                )
                return None

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                last_error = exc
                logging.debug(f"[{self.name}] HTML fetch failed for {item_url}: {exc}")
                continue

            logging.debug(
                f"[{self.name}] HTML fetch succeeded for {item_url} "
                f"(bytes={len(response.text)})"
            )
            return BeautifulSoup(response.text, "html.parser")

        logging.debug(
            f"[{self.name}] HTML fetch exhausted retries for {item_url}. "
            f"Last error: {last_error}"
        )
        return None

    def _scrape_item_from_html(self, item_url: str) -> dict | None:
        soup = self._fetch_item_soup(item_url)
        if not soup:
            return None

        title = self._extract_html_title(soup)
        price = self._extract_html_price(soup)
        description = self._extract_html_description(soup)
        images = self._extract_html_images(soup)
        seller = self._extract_html_seller(soup)

        if not any([title, price, description, images, seller]):
            logging.debug(f"[{self.name}] HTML scrape returned no usable data for {item_url}")
            return None

        html_data: dict = {"url": item_url, "images": images or []}
        if title:
            html_data["title"] = title
        else:
            logging.debug(f"[{self.name}] HTML scrape missing title for {item_url}")
        if price:
            html_data["price"] = price
        else:
            logging.debug(f"[{self.name}] HTML scrape missing price for {item_url}")
        if description:
            html_data["description"] = description
        else:
            logging.debug(f"[{self.name}] HTML scrape missing description for {item_url}")
        if seller:
            html_data["seller"] = seller
        else:
            logging.debug(f"[{self.name}] HTML scrape missing seller for {item_url}")
        logging.debug(
            f"[{self.name}] HTML scrape extracted {len(html_data['images'])} images for {item_url}"
        )
        return html_data

    def _get_html_session(self) -> requests.Session:
        session = getattr(self._html_session_local, "session", None)
        if session is None:
            session = requests.Session()
            headers = get_header()
            if self.user_agent:
                headers["User-Agent"] = self.user_agent
            session.headers.update(headers)
            logging.debug(f"[{self.name}] Initialized new HTML session with headers {session.headers}")
            self._html_session_local.session = session
        return session

    def _extract_html_title(self, soup: BeautifulSoup) -> str | None:
        selectors = ("[data-testid='item-title']", "main h1", "h1")
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = node.get_text(strip=True)
                if text:
                    return text
        return self._get_meta_content(soup, ("og:title", "twitter:title"))

    def _extract_html_price(self, soup: BeautifulSoup) -> str | None:
        price_el = soup.select_one('[data-testid="item-price"]')
        if price_el:
            price = price_el.get_text(" ", strip=True).replace("\xa0", " ")
            if price:
                return price
        amount = self._get_meta_content(soup, ("product:price:amount", "og:price:amount"))
        currency = self._get_meta_content(soup, ("product:price:currency", "og:price:currency"))
        if amount and currency:
            return f"{amount} {currency}"
        return amount

    def _extract_html_description(self, soup: BeautifulSoup) -> str | None:
        selectors = (
            '[data-testid="item-description"]',
            '[data-testid="item-description-content"]',
            '[data-testid="description-content"]',
            '[data-testid="item-details-description"]',
        )
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                text = node.get_text("\n", strip=True)
                if text:
                    return text
        meta_description = self._get_meta_content(soup, ("description", "og:description"))
        if meta_description:
            parts = meta_description.split(" - ", 1)
            if len(parts) == 2 and parts[1].strip():
                return parts[1].strip()
            return meta_description.strip()
        return None

    def _extract_html_images(self, soup: BeautifulSoup) -> List[str]:
        images: List[str] = []
        seen: set[str] = set()
        for img in soup.select("img[data-testid^='item-photo']"):
            src = img.get("src") or img.get("data-src")
            if not src:
                srcset = img.get("srcset")
                if srcset:
                    src = srcset.split(" ")[0]
            if not src:
                continue
            if src not in seen:
                images.append(src)
                seen.add(src)
        logging.debug(f"[{self.name}] HTML image extraction found {len(images)} DOM images")
        if not images:
            fallback = self._get_meta_content(soup, ("og:image", "twitter:image"))
            if fallback:
                images.append(fallback)
                logging.debug(f"[{self.name}] Falling back to meta image {fallback}")
        return images

    def _extract_html_seller(self, soup: BeautifulSoup) -> str | None:
        seller_el = soup.select_one('[data-testid="profile-username"]')
        if seller_el:
            text = seller_el.get_text(strip=True)
            if text:
                return text
        return None

    def _get_meta_content(self, soup: BeautifulSoup, keys: tuple[str, ...]) -> str | None:
        for key in keys:
            tag = soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                content = tag.get("content", "").strip()
                if content:
                    return content
            tag = soup.find("meta", attrs={"property": key})
            if tag and tag.get("content"):
                content = tag.get("content", "").strip()
                if content:
                    return content
        return None

    def _coerce_int_option(
        self,
        options: Dict[str, str] | None,
        key: str,
        default: int,
        min_value: int,
        max_value: int
    ) -> int:
        if not options:
            return default
        raw_value = options.get(key)
        if raw_value is None:
            return default
        try:
            parsed = int(raw_value)
        except ValueError:
            logging.warning(f"[{self.name}] Invalid value for {key}: {raw_value}. Using default {default}.")
            return default
        return max(min_value, min(parsed, max_value))
