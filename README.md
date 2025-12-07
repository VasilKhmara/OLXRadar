![OLXRadar](https://i.imgur.com/umVlxwV.jpeg)
# OLXRadar
Get notified instantly of new listings on OLX with this Python app that sends alerts via Telegram and email.

## Prerequisites

Before running the app, you must have the following installed:

* Python 3.x
* A Gmail account and a Gmail app password (see below how to get one)
* A Telegram bot (see below how to create one)

## Installation

1. Clone/download this repository to your local machine.
2. Open a terminal and navigate to the project directory.
3. Create a new virtual environment by running the following command:
   ```
   python3 -m venv venv
   ```
4. Activate the virtual environment:
   ```
   source venv/bin/activate
   ```
5. Install the required packages:
   ```
   pip install -r requirements.txt
   ```
6. Setup your Telegram bot:
   1. Create a new bot by talking to the [BotFather](https://t.me/BotFather).
   2. Copy the bot token.
   3. Send a message to your bot and get the chat ID.
   4. Copy the chat ID.
   5. Create a file named `.env` in the project directory.
   4. Add the following lines to the `.env` file:
      ```
      TELEGRAM_BOT_TOKEN="your_token_here"
      TELEGRAM_CHAT_ID="your_chat_id_here"
      ```
      [👉 detailed instructions on how to get the bot token and chat ID](https://12ft.io/proxy?q=https%3A%2F%2Fmedium.com%2Fcodex%2Fusing-python-to-send-telegram-messages-in-3-simple-steps-419a8b5e5e2)



7. Add a product URL to monitor:
   1. Search for a product on any supported marketplace (OLX and Vinted are included out of the box).
   2. Copy the URL of the search results page (e.g. `https://www.vinted.fr/vetement?order=newest_first&price_to=60&currency=EUR`).
   3. Add the URL to `target_urls.txt`, located in the project directory. Add one URL per line. You can mix different marketplaces; the scraper orchestrator will route each URL to the right platform worker. You can also append inline options using the syntax `|| key=value,key2=value2` (see below).

![How to get a search url](https://i.imgur.com/9tEANnp.png)

## Adding Support for New Marketplaces

OLXRadar now uses a `ScraperOrchestrator` (see `scraper_manager.py`) plus dedicated scraper modules under `scrapers/`. To plug in another platform:

1. Create a class that inherits from `MarketplaceScraper` (see `scrapers/base.py`) and place it in the `scrapers/` package.
2. Implement `collect_listings` (returns a list of `ListingCandidate` objects; optionally pre-populate each ad’s data to avoid extra detail fetches) and `get_ad_data` (returns a normalized ad dictionary for a single URL).
3. Register the scraper by passing it to `ScraperOrchestrator(scrapers=[ExistingScrapers..., YourScraper()])` or by calling `register_scraper`.

All target URLs are loaded once per cycle and dispatched to the scraper that matches the domain, so new platforms can operate safely alongside OLX. Each ad dict emitted by the orchestrator includes a `platform` key so downstream workflows can tell where it came from, and scrapers can optionally preload the full ad payload (see the `ListingCandidate` dataclass) to skip extra detail requests. Scrapers are expected to consume search URLs sorted by *newest*: after every page they stop paginating the moment they encounter an already-seen listing, which keeps the runtime tight even for very active feeds.

### Target URL Options

Add options to any line in `target_urls.txt` by appending `|| key=value,key2=value2`. Examples:

- `https://www.vinted.fr/vetement?order=newest_first||page_size=48,max_pages=10`
- `https://www.olx.pl/d/nieruchomosci/mieszkania/`

Currently supported options (platform-specific):

- `page_size` (Vinted): overrides the number of items fetched per API page (1–96).
- `max_pages` (Vinted): caps the number of pages fetched per cycle (1–32).

## Watching Vinted via HTML scraping

Vinted support is handled by the built-in `VintedScraper`, which pulls catalog pages with `requests`/`BeautifulSoup` and extracts each card URL directly from the HTML. Detail pages are then re-scraped when needed to gather title, price, description and photos.

The scraper honors `page_size` (per-page card count, 1–96) and `max_pages` (max catalog pages fetched per cycle, 1–32). Because we stop when we hit known listings, each run only explores the freshest tail of a sorted search.
