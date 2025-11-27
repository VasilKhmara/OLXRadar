from __future__ import annotations

import logging
import logging_config  # noqa: F401
import re
import threading
import time
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from scrapers.base import ListingCandidate, MarketplaceScraper
from utils import get_header

try:  # pragma: no cover - optional dependency
    from pyVinted import Vinted  # type: ignore
    from pyVinted.requester import requester as vinted_requester  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise ImportError("pyVinted is required for Vinted scraping. Install it via `pip install pyVinted`.") from exc


class VintedScraper(MarketplaceScraper):
    """Scraper implementation backed by the pyVinted client."""

    name = "vinted"
    max_allowed_page_size = 20
    max_allowed_pages = 10

    def __init__(
        self,
        page_size: int = 20,
        max_pages: int = 10,
        user_agent: str | None = None
    ) -> None:
        self.client = Vinted()
        self.user_agent = user_agent
        if user_agent:
            vinted_requester.session.headers.update({"User-Agent": user_agent})
        self.page_size = min(max(1, page_size), self.max_allowed_page_size)
        self.max_pages = min(max(1, max_pages), self.max_allowed_pages)
        self._html_session_local = threading.local()

    def supports(self, target_url: str) -> bool:
        domain = urlparse(target_url).netloc.lower()
        return "vinted" in domain

    def collect_listings(
        self,
        target_url: str,
        options: Dict[str, str] | None = None,
        is_known: Callable[[str], bool] | None = None
    ) -> List[ListingCandidate]:
        parsed_url = urlparse(target_url)
        fallback_domain = parsed_url.netloc or "www.vinted.com"
        scheme = parsed_url.scheme or "https"
        page = 1
        collected: List[ListingCandidate] = []
        page_size = self._coerce_int_option(options, "page_size", self.page_size, 1, self.max_allowed_page_size)
        max_pages = self._coerce_int_option(options, "max_pages", self.max_pages, 1, self.max_allowed_pages)

        logging.info(f"[{self.name}] Starting scrape for {target_url}")
        while page <= max_pages:
            try:
                logging.debug(
                    f"[{self.name}] Fetching page {page} (size={page_size}) for {target_url}"
                )
                items = self._search_with_retries(target_url, page_size, page)
            except Exception as exc:  # pragma: no cover
                logging.error(f"[{self.name}] Failed to fetch page {page} for {target_url}: {exc}")
                break

            if not items:
                logging.info(f"[{self.name}] No items returned for page {page}.")
                break

            page_contains_known = False
            logging.debug(f"[{self.name}] Page {page} returned {len(items)} items")
            for idx, item in enumerate(items, start=1):
                ad_data = self._build_ad_from_item(item, scheme, fallback_domain)
                if not ad_data:
                    logging.debug(f"[{self.name}] Item #{idx} missing required fields, skipping.")
                    continue
                logging.debug(f"[{self.name}] Item #{idx} URL: {ad_data['url']}")
                if is_known and is_known(ad_data["url"]):
                    page_contains_known = True
                    logging.debug(f"[{self.name}] Item #{idx} already processed, marking page as known.")
                    continue

                html_snapshot = self._scrape_item_from_html(ad_data["url"])
                if html_snapshot:
                    previous_images = len(ad_data.get("images") or [])
                    ad_data = self._merge_payloads(ad_data, html_snapshot)
                    logging.debug(
                        f"[{self.name}] Item #{idx} HTML enrichment -> images: "
                        f"{previous_images} -> {len(ad_data.get('images') or [])}"
                    )
                else:
                    logging.debug(f"[{self.name}] Item #{idx} HTML enrichment failed; keeping API payload.")

                collected.append(ListingCandidate(url=ad_data["url"], data=ad_data))
                logging.debug(f"[{self.name}] Item #{idx} accepted. Total listings: {len(collected)}")

            if len(items) < page_size:
                logging.info(f"[{self.name}] Received short page ({len(items)} items). Stopping pagination.")
                break

            if page_contains_known:
                logging.info(f"[{self.name}] Encountered known listings; stopping pagination early.")
                break

            page += 1

        logging.info(f"[{self.name}] Finished scraping. Total unique items: {len(collected)}")
        return collected

    def get_ad_data(self, ad_url: str) -> dict | None:
        """Return a fully hydrated listing payload for the provided URL."""
        html_data = self._scrape_item_from_html(ad_url)
        if html_data and self._payload_has_core_fields(html_data):
            logging.debug(f"[{self.name}] HTML scrape succeeded for {ad_url}")
            return html_data

        logging.debug(f"[{self.name}] HTML scrape incomplete for {ad_url}, falling back to API.")
        api_data = self._fetch_item_via_api(ad_url)
        if not api_data and html_data:
            logging.debug(f"[{self.name}] API fallback missing for {ad_url}; returning HTML snapshot only.")
            return html_data
        if api_data and html_data:
            return self._merge_payloads(api_data, html_data)
        return api_data

    def _build_ad_from_item(self, item: dict | object | None, scheme: str, fallback_domain: str) -> dict | None:
        payload = self._normalize_item_payload(item)
        if not payload:
            logging.debug(f"[{self.name}] Unsupported item payload type: {type(item)}")
            return None

        url = payload.get("url") or payload.get("path")
        if isinstance(url, str) and url.startswith("/"):
            url = f"{scheme}://{fallback_domain or 'www.vinted.com'}{url}"
        if not url:
            url = self._build_url_from_parts(payload, scheme, fallback_domain)
        if not url:
            logging.debug(f"[{self.name}] Unable to derive URL for payload: {payload.get('id')}")
            return None

        title = payload.get("title") or payload.get("name")
        description = (
            payload.get("description")
            or payload.get("content")
            or self._extract_item_box_description(payload)
        )
        price = self._format_price(payload)

        if not all([title, description, price]):
            missing = ", ".join(
                [
                    label
                    for label, value in (("title", title), ("description", description), ("price", price))
                    if not value
                ]
            )
            logging.debug(f"[{self.name}] Skipping {url} due to missing fields: {missing}")
            return None

        ad_data = {
            "title": title,
            "price": price,
            "url": url,
            "description": description,
            "images": self._extract_images(payload),
        }
        logging.info(
            f"[{self.name}] Built ad data for {url} "
            f"(photos={len(payload.get('photos') or [])}, images={len(ad_data['images'])})"
        )
        seller = self._extract_seller(payload)
        if seller:
            ad_data["seller"] = seller
        return ad_data

    def _merge_payloads(self, base: dict, override: dict) -> dict:
        merged = dict(base)
        for key, value in override.items():
            if key == "images":
                if value:
                    merged[key] = value
                continue
            if value:
                merged[key] = value
        return merged

    def _payload_has_core_fields(self, payload: dict) -> bool:
        return all(payload.get(field) for field in ("title", "description", "price"))

    def _fetch_item_via_api(self, ad_url: str) -> dict | None:
        parsed = urlparse(ad_url)
        scheme = parsed.scheme or "https"
        domain = parsed.netloc or "www.vinted.com"
        try:
            items = self.client.items.search(ad_url, 1, 1)
        except Exception as exc:  # pragma: no cover
            logging.error(f"[{self.name}] API fallback failed for {ad_url}: {exc}")
            return None
        if not items:
            logging.debug(f"[{self.name}] API fallback returned no items for {ad_url}")
            return None
        return self._build_ad_from_item(items[0], scheme, domain)

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

    def _search_with_retries(self, target_url: str, page_size: int, page: int) -> List[Any]:
        delays = (0, 1, 2, 4)
        last_error: Exception | None = None

        for attempt, delay in enumerate(delays, start=1):
            if delay:
                logging.debug(
                    f"[{self.name}] Sleeping {delay}s before search retry #{attempt} "
                    f"for {target_url}"
                )
                time.sleep(delay)
            try:
                logging.debug(
                    f"[{self.name}] Calling pyVinted search attempt {attempt}/{len(delays)} "
                    f"for {target_url}"
                )
                return self.client.items.search(target_url, page_size, page)
            except Exception as exc:
                last_error = exc
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code == 403:
                    logging.error(
                        f"[{self.name}] pyVinted search returned 403 for {target_url}; "
                        "skipping further retries."
                    )
                    break
                if status_code in (401, 429):
                    logging.warning(
                        f"[{self.name}] pyVinted search returned {status_code} for {target_url} "
                        f"(attempt {attempt}/{len(delays)})"
                    )
                    self._refresh_vinted_cookies(target_url)
                    if attempt == len(delays):
                        break
                    continue
                logging.error(f"[{self.name}] pyVinted search failed for {target_url}: {exc}")
                break

        if last_error:
            logging.error(
                f"[{self.name}] Exhausted pyVinted search retries for {target_url}: {last_error}"
            )
        return []

    def _refresh_vinted_cookies(self, target_url: str) -> None:
        domain = urlparse(target_url).netloc or "www.vinted.com"
        refreshed = False
        if vinted_requester:
            try:
                vinted_requester.setLocale(domain)
                vinted_requester.setCookies()
                refreshed = True
                logging.debug(f"[{self.name}] Refreshed pyVinted cookies for {domain}")
            except Exception as exc:  # pragma: no cover
                logging.debug(f"[{self.name}] pyVinted cookie refresh failed: {exc}")
        if refreshed:
            return
        session = self._get_html_session()
        base_url = f"https://{domain}/"
        try:
            logging.debug(f"[{self.name}] Warming cookies via HEAD {base_url}")
            session.head(base_url, timeout=10)
        except requests.RequestException as exc:  # pragma: no cover
            logging.debug(f"[{self.name}] Cookie warm-up failed for {base_url}: {exc}")

    def _fetch_item_soup(self, item_url: str) -> BeautifulSoup | None:
        session = self._get_html_session()
        delays = (0, 1, 2, 4)
        last_error: Exception | None = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                logging.debug(f"[{self.name}] Sleeping {delay}s before HTML retry #{attempt} for {item_url}")
                time.sleep(delay)
            try:
                logging.debug(f"[{self.name}] Fetching HTML for {item_url} (attempt {attempt}/{len(delays)})")
                response = session.get(item_url, timeout=15)
            except requests.RequestException as exc:
                last_error = exc
                logging.debug(f"[{self.name}] HTML fetch exception for {item_url}: {exc}")
                continue

            if response.status_code == 429:
                last_error = requests.HTTPError("429 Too Many Requests")
                logging.debug(
                    f"[{self.name}] HTML fetch hit 429 for {item_url} on attempt {attempt}. "
                    "Will retry with backoff." if attempt < len(delays) else
                    f"[{self.name}] HTML fetch hit 429 for {item_url} on final attempt."
                )
                if attempt == len(delays):
                    break
                continue

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

    def _build_url_from_parts(self, item: dict, scheme: str, domain: str) -> str | None:
        item_id = item.get("id")
        if not item_id:
            return None
        path = item.get("path")
        if isinstance(path, str):
            if path.startswith("http"):
                return path
            if not path.startswith("/"):
                path = f"/{path}"
            return f"{scheme}://{domain or 'www.vinted.com'}{path}"
        slug = item.get("slug") or item.get("title") or ""
        safe_slug = re.sub(r"[^\w\-]+", "-", slug.lower()).strip("-")
        path = f"/items/{item_id}"
        if safe_slug:
            path = f"{path}-{safe_slug}"
        return f"{scheme}://{domain or 'www.vinted.com'}{path}"

    def _format_price(self, payload: dict) -> str | None:
        price_candidates = [
            payload.get("price"),
            payload.get("total_item_price"),
            payload.get("converted_price"),
        ]
        price_info = next((candidate for candidate in price_candidates if isinstance(candidate, dict)), None)
        if not price_info:
            scalar_price = next(
                (candidate for candidate in price_candidates if isinstance(candidate, (int, float, str)) and candidate),
                None,
            )
            if scalar_price is None:
                return None
            amount = str(scalar_price).strip()
            if not amount:
                return None
            currency = payload.get("currency") or payload.get("currency_code")
            return f"{amount} {currency}".strip() if currency else amount
        amount = (
            price_info.get("amount")
            or price_info.get("value")
            or price_info.get("number")
        )
        currency = price_info.get("currency") or price_info.get("currency_code")
        if amount and currency:
            return f"{amount} {currency}"
        if amount:
            return str(amount)
        return None

    def _extract_item_box_description(self, payload: dict) -> str | None:
        box = payload.get("item_box")
        if isinstance(box, dict):
            return box.get("accessibility_label") or box.get("first_line")
        return None

    def _normalize_item_payload(self, item: dict | object | None) -> dict | None:
        if item is None:
            return None
        if isinstance(item, dict):
            return dict(item)

        payload: dict = {}
        raw_data = getattr(item, "raw_data", None)
        if isinstance(raw_data, dict):
            payload.update(raw_data)

        attribute_candidates = (
            "id",
            "title",
            "description",
            "content",
            "price",
            "converted_price",
            "total_item_price",
            "currency",
            "url",
            "slug",
            "path",
            "photos",
            "photo",
            "user",
            "seller",
            "item_box",
        )
        for attr in attribute_candidates:
            if hasattr(item, attr):
                value = getattr(item, attr)
                if value is not None:
                    payload.setdefault(attr, value)
        return payload or None

    def _extract_seller(self, payload: dict) -> str | None:
        seller_info = payload.get("seller") or payload.get("user") or {}
        if not isinstance(seller_info, dict):
            return None
        return seller_info.get("login") or seller_info.get("username") or seller_info.get("name")

    def _extract_images(self, payload: dict) -> List[str]:
        images: List[str] = []
        photos = payload.get("photos") or []
        logging.debug(f"[{self.name}] Extracting images from {len(photos)} photo entries.")
        for idx, photo in enumerate(photos, start=1):
            if not isinstance(photo, dict):
                logging.debug(f"[{self.name}] Photo #{idx} is not a dict, skipping: {photo}")
                continue
            photo_urls = self._gather_urls_from_node(photo)
            logging.debug(f"[{self.name}] Photo #{idx} yielded URLs: {photo_urls}")
            for url in photo_urls:
                if url not in images:
                    images.append(url)
        cover_photo = payload.get("photo") or {}
        if not images and isinstance(cover_photo, dict):
            fallback_urls = self._gather_urls_from_node(cover_photo)
            logging.debug(f"[{self.name}] Using fallback cover photo URLs: {fallback_urls}")
            for url in fallback_urls:
                if url not in images:
                    images.append(url)
        logging.info(f"[{self.name}] Extracted {len(images)} image URLs for payload id {payload.get('id')}")
        return images

    def _gather_urls_from_node(self, node: Any) -> List[str]:
        urls: List[str] = []
        if isinstance(node, str):
            if node.startswith("http"):
                urls.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                for child_url in self._gather_urls_from_node(value):
                    if child_url not in urls:
                        urls.append(child_url)
        elif isinstance(node, list):
            for item in node:
                for child_url in self._gather_urls_from_node(item):
                    if child_url not in urls:
                        urls.append(child_url)
        return urls

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

