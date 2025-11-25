from __future__ import annotations

import logging
import logging_config  # noqa: F401  # ensure logging config is loaded
import re
from typing import Callable, Dict, List, Sequence
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, ResultSet, Tag

from scrapers.base import ListingCandidate, MarketplaceScraper
from utils import get_header


class OlxScraper(MarketplaceScraper):
    """Scraper for OLX marketplaces (Romania, Ukraine, Poland)."""

    name = "olx"
    supported_domains = ("www.olx.ua", "www.olx.pl", "www.olx.ro")

    def __init__(self) -> None:
        self.headers = get_header()
        self.schema = "https"

    def collect_listings(
        self,
        target_url: str,
        options: Dict[str, str] | None = None,
        is_known: Callable[[str], bool] | None = None
    ) -> List[ListingCandidate]:
        listings: List[ListingCandidate] = []
        parsed_target = urlparse(target_url)
        target_netloc = parsed_target.netloc

        if target_netloc not in self.supported_domains:
            raise ValueError(
                f"Bad URL! OLXRadar is configured to process {', '.join(self.supported_domains)} links only."
            )

        current_page = 1
        last_page = None

        logging.info(f"[{self.name}] Starting scraping for target URL: {target_url}")
        while True:
            logging.debug(f"[{self.name}] --- PAGE {current_page} ---")
            logging.info(f"[{self.name}] Scraping page: {current_page}")
            page_url = self._build_page_url(target_url, current_page)
            logging.debug(f"[{self.name}] Fetching page URL: {page_url}")
            parsed_content = self._parse_content(page_url)
            last_page = self._get_last_page(parsed_content)
            ads = self._get_ads(parsed_content)
            if ads is None:
                logging.info("[olx] No ads found on page.")
                return listings
            logging.info(f"[{self.name}] Found {len(ads)} ads on page {current_page}")
            page_contains_known = False
            for idx, ad in enumerate(ads, start=1):
                link_href = self._find_listing_link(ad)
                logging.debug(f"[{self.name}] Raw ad #{idx} href: {link_href!r}")
                if not link_href:
                    anchors = ad.find_all("a", href=True)
                    logging.debug(f"[{self.name}] Ad #{idx} anchor count: {len(anchors)}")
                    if anchors:
                        logging.debug(f"[{self.name}] Sample anchor hrefs: {[a.get('href') for a in anchors[:3]]}")
                    else:
                        snippet = str(ad)
                        logging.debug(f"[{self.name}] Ad #{idx} snippet (truncated): {snippet[:800]}")
                    logging.debug(f"[{self.name}] Ad #{idx} has no usable link, skipping.")
                    continue
                if not self._is_internal_url(link_href, target_netloc):
                    logging.debug(f"[{self.name}] Ad #{idx} link is external, skipping: {link_href}")
                    continue
                if not self._is_relevant_url(link_href):
                    logging.debug(f"[{self.name}] Ad #{idx} link marked not relevant, skipping: {link_href}")
                    continue
                if self._is_relative_url(link_href):
                    link_href = f"{self.schema}://{target_netloc}{link_href}"
                logging.debug(f"[{self.name}] Normalized ad #{idx} URL: {link_href}")
                if is_known and is_known(link_href):
                    page_contains_known = True
                    logging.debug(f"[{self.name}] Ad #{idx} already in DB, marking page as containing known items.")
                    continue
                listings.append(ListingCandidate(url=link_href))
                logging.debug(f"[{self.name}] Ad #{idx} accepted. Total listings so far: {len(listings)}")
            if last_page is None or current_page >= last_page:
                logging.info(f"[{self.name}] Reached last page or no pagination.")
                break
            if page_contains_known:
                logging.info(f"[{self.name}] Encountered known listings; stopping pagination early.")
                break
            current_page += 1
        logging.info(f"Finished scraping. Total unique ads found: {len(listings)}")
        return listings

    def get_ad_data(self, ad_url: str) -> dict | None:
        logging.info(f"Processing {ad_url}")
        content = self._parse_content(ad_url)

        if content is None:
            return None

        title = self._extract_text(
            content,
            selectors=[
                '[data-cy="ad_title"]',
                '[data-testid="ad-title"]',
                'h4.css-1au435n',
                'h1.css-1soizd2',
            ],
        )
        price = self._extract_text(
            content,
            selectors=[
                '[data-testid="ad-price-container"]',
                '[data-testid="ad-price"]',
                'h3.css-yauxmy',
                'h3.css-ddweki',
            ],
        )
        description = self._extract_text(
            content,
            selectors=[
                '[data-cy="ad_description"]',
                '[data-testid="ad_description"]',
                'div.css-19duwlz',
                'div.css-bgzo2k',
            ],
            separator="\n",
        )
        seller = self._extract_text(
            content,
            selectors=[
                '[data-testid="seller-card"] h4',
                '[data-testid="seller-contact"] h4',
                'h4.css-14tb3q5',
                'h4.css-1lcz6o7',
            ],
        )

        images = self._extract_images(content)

        if any(item is None for item in [title, price, description]):
            missing = []
            if title is None:
                missing.append("title")
            if price is None:
                missing.append("price")
            if description is None:
                missing.append("description")
            logging.warning(f"Missing required data for {ad_url}: {', '.join(missing)}")
            return None

        ad_data = {
            "title": title,
            "price": price,
            "url": ad_url,
            "description": description,
            "images": images,
        }
        if seller:
            ad_data["seller"] = seller
        logging.info(f"Successfully extracted data for {ad_url}")
        return ad_data

    def _parse_content(self, target_url: str) -> BeautifulSoup | None:
        try:
            response = requests.get(target_url, headers=self.headers, timeout=60)
            response.raise_for_status()
        except requests.exceptions.RequestException as error:
            logging.error(f"Connection error: {error}")
            return None
        return BeautifulSoup(response.text, "html.parser")

    def _get_ads(self, parsed_content: BeautifulSoup | None) -> ResultSet[Tag] | None:
        if parsed_content is None:
            return None
        selectors = [
            '[data-cy="l-card"]',
            '[data-testid="l-card"]',
            'div[data-cy="ad-card"]',
            'div.css-1sw7q4x',
        ]
        for selector in selectors:
            ads = parsed_content.select(selector)
            if ads:
                logging.debug(f"[{self.name}] Using selector {selector!r}, found {len(ads)} ads.")
                return ads
        logging.debug(f"[{self.name}] No ads found with any known selector.")
        return None

    def _get_last_page(self, parsed_content: BeautifulSoup | None) -> int | None:
        if parsed_content is None:
            return None
        pagination_ul = parsed_content.find("ul", class_="pagination-list")
        if pagination_ul is None:
            return None
        pages = pagination_ul.find_all("li", class_="pagination-item")
        if not pages:
            return None
        return int(pages[-1].text)

    def _find_listing_link(self, ad: Tag) -> str | None:
        selectors = [
            'a[data-cy="listing-ad-title"]',
            'a[data-testid="ad-title"]',
            'a[data-cy="ad-card-link"]',
            'a[data-testid="ad-card-link"]',
            'a.css-rc5s2u',
            'a.css-1tqlkj0',
        ]
        for selector in selectors:
            link = ad.select_one(selector)
            if link and link.has_attr("href"):
                logging.debug(f"[{self.name}] Found link via selector {selector!r}: {link['href']}")
                return link["href"]
        fallback_link = ad.find("a", href=True)
        if fallback_link:
            logging.debug(f"[{self.name}] Using fallback <a> for link: {fallback_link['href']}")
            return fallback_link["href"]
        # Final fallback: regex search for href attribute (handles non-standard elements)
        html = str(ad)
        match = re.search(r'href=["\\\']([^"\\\']+)["\\\']', html)
        if match:
            href = match.group(1)
            logging.debug(f"[{self.name}] Fallback regex found href: {href}")
            return href
        return None

    def _is_relevant_url(self, url: str) -> bool:
        """
        Determine whether a URL is relevant for monitoring.

        Historically OLX added helper ads with a query segment like
        `?reason=extended-region` for nearby regions. Those are not useful.
        However, many legitimate ads now include benign query params
        (e.g. tracking), so we only filter out the clearly synthetic ones.
        """
        segments = urlparse(url)
        if not segments.query:
            return True
        if "reason=extended-region" in segments.query:
            return False
        return True

    def _is_internal_url(self, url: str, domain: str) -> bool:
        if self._is_relative_url(url):
            return True
        parsed_url = urlparse(url)
        return parsed_url.netloc in self.supported_domains

    def _is_relative_url(self, url: str) -> bool:
        parsed_url = urlparse(url)
        if not parsed_url.netloc:
            return True
        return bool(re.search(r"^\/[\w.\-\/]+", url))

    def _build_page_url(self, target_url: str, page: int) -> str:
        parsed = urlparse(target_url)
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query_params["page"] = str(page)
        new_query = urlencode(query_params, doseq=True)
        new_url = urlunparse(parsed._replace(query=new_query))
        return new_url

    def _extract_text(self, content: BeautifulSoup, selectors: Sequence[str], separator: str = " ") -> str | None:
        for selector in selectors:
            node = content.select_one(selector)
            if node:
                text = node.get_text(strip=True, separator=separator)
                if text:
                    return text
        return None

    def _extract_images(self, content: BeautifulSoup) -> List[str]:
        images: List[str] = []
        selectors = [
            'img[data-testid*="swiper-image"]',
            'img[data-cy="gallery-image"]',
            'img[data-testid="ad-image"]',
        ]
        for selector in selectors:
            for image_tag in content.select(selector):
                src = image_tag.get("src")
                if src and src not in images:
                    images.append(src)
        if not images:
            image_tags = content.find_all("img", {"data-testid": re.compile(r"swiper-image")})
            for image_tag in image_tags:
                src = image_tag.get("src")
                if src and src not in images:
                    images.append(src)
        return images

