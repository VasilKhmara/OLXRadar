import os
import logging
import logging_config
import time
import argparse
from multiprocessing import Pool
from scraper_manager import OlxScraper
from database_manager import DatabaseManager
from notification_manager import Messenger
from utils import BASE_DIR


scraper = OlxScraper()
db = DatabaseManager()


def load_target_urls() -> list:
    """
    Fetch the list of URLs to monitor from the file 'target_urls.txt',
    which is located in the same directory as the script.

    Returns:
        list: list of URLs from which to collect data. If the
        file does not exist, it creates it and returns an empty list.

    """
    file_path = os.path.join(BASE_DIR, "target_urls.txt")
    user_message = f"The file 'target_urls.txt' has been created. Add " \
        + f"in it at least one URL to monitor for new ads. Add 1 URL per line."
    try:
        with open(file_path) as f:
            target_urls = [line.strip() for line in f]
    except FileNotFoundError:
        logging.info(user_message)
        open(file_path, "w").close()
        target_urls = []
    if not target_urls:
        logging.info(user_message)
    return target_urls


def get_new_ads_urls(all_urls: list) -> list:
    """
    Returns a list of new ad URLs (not found in the database). 

    Args:
        all_urls (list): list of URLs to be matched against the database.

    Returns:
        new_urls (list): List of URLs not found in the database.
    """
    new_urls = []
    if all_urls:
        for url in all_urls:
            if not db.url_exists(url):
                new_urls.append(url)
    return new_urls


def get_new_ads_urls_for_url(target_url: str) -> list:
    """
    Extracts ads for a specific URL and filters out previously processed ads.

    Args:
        target_url (str): A string representing the URL for which new ads should be retrieved.

    Returns:
        List[str]: A list of URLs representing new ads retrieved from the monitored URL.
    """

    try:
        ads_urls = scraper.scrape_ads_urls(target_url)
    except ValueError as error:
        logging.error(error)
        return []
    return get_new_ads_urls(ads_urls)


def main() -> None:
    """
    Main function. Collects and processes ads
    and sends notifications by email and Telegram.
    """
    parser = argparse.ArgumentParser(description="OLX Radar - Scrape OLX for new ads.")
    parser.add_argument(
        "--interval", 
        type=int, 
        default=2, 
        help="Interval in minutes between scrape cycles (default: 15)"
    )
    args = parser.parse_args()
    
    scrape_interval_seconds = args.interval * 60
    logging.info(f"Starting OLX Radar. Scrape interval: {args.interval} minutes.")

    while True:
        try:
            target_urls = load_target_urls()
            for target_url in target_urls:
                logging.info(f"Processing target URL: {target_url}")
                ads_urls = get_new_ads_urls_for_url(target_url)
                logging.info(f"Total potential ads found: {len(ads_urls)}")

                # Filter out the already processed ads
                new_ads_urls = get_new_ads_urls(ads_urls)
                logging.info(f"New ads found (not in DB): {len(new_ads_urls)}")
                if not new_ads_urls:
                    logging.info("No new ads to process.")
                    continue

                # Process ads in parallel, for increased speed
                logging.info(f"Starting detailed scraping for {len(new_ads_urls)} new ads...")
                with Pool(10) as pool:
                    new_ads = pool.map(scraper.get_ad_data, new_ads_urls)
                new_ads = list(filter(None, new_ads))
                logging.info(f"Successfully scraped details for {len(new_ads)} ads.")
                
                # Log details of each new ad
                for idx, ad in enumerate(new_ads, 1):
                    logging.info(f"--- New Ad #{idx} ---")
                    logging.info(f"Title: {ad.get('title', 'N/A')}")
                    logging.info(f"Price: {ad.get('price', 'N/A')}")
                    logging.info(f"URL: {ad.get('url', 'N/A')}")
                    description = ad.get('description', 'N/A')
                    # Truncate description if too long for readability
                    if description != 'N/A' and len(description) > 200:
                        description = description[:200] + "..."
                    logging.info(f"Description: {description}")
                    if 'seller' in ad and ad['seller']:
                        logging.info(f"Seller: {ad['seller']}")
                    logging.info("---")

                if new_ads:
                    logging.info(f"Sending notifications for {len(new_ads)} new ads...")
                    for idx, ad in enumerate(new_ads, 1):
                        try:
                            message_subject, message_body = Messenger.generate_single_ad_notification(ad)
                            Messenger.send_telegram_message(message_subject, message_body)
                            logging.info(f"Notification sent for ad #{idx}: {ad.get('title', 'N/A')[:50]}")
                        except Exception as e:
                            logging.error(f"Failed to send notification for ad #{idx}: {e}")
                    logging.info(f"All notifications sent ({len(new_ads)} total).")

                # Add the processed ads to database
                logging.info("Updating database with new URLs...")
                for url in new_ads_urls:
                    db.add_url(url)
                logging.info("Database updated.")
            
            logging.info(f"Cycle completed. Sleeping for {args.interval} minutes...")
            time.sleep(scrape_interval_seconds)
            
        except KeyboardInterrupt:
            logging.info("Stopping OLX Radar...")
            break
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            logging.info(f"Retrying in {args.interval} minutes...")
            time.sleep(scrape_interval_seconds)


if __name__ == "__main__":
    main()
