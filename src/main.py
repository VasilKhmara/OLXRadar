import logging
import logging_config
import time
import argparse
from scraper_manager import ScraperOrchestrator
from database_manager import DatabaseManager
from notification_manager import Messenger


def main() -> None:
    """
    Main function. Collects and processes ads
    and sends notifications by email and Telegram.
    """
    parser = argparse.ArgumentParser(description="OLX Radar - Scrape OLX for new ads.")
    parser.add_argument(
        "--interval", 
        type=int, 
        default=1, 
        help="Interval in minutes between scrape cycles (default: 1)"
    )
    args = parser.parse_args()
    
    scrape_interval_seconds = args.interval * 60
    db = DatabaseManager()
    scraper = ScraperOrchestrator(database=db)
    logging.info(f"Starting OLX Radar. Scrape interval: {args.interval} minutes.")

    while True:
        try:
            new_ads = scraper.collect_new_ads()
            if not new_ads:
                logging.info("No new ads to process.")
            else:
                logging.info(f"Successfully scraped details for {len(new_ads)} ads.")
                
                for idx, ad in enumerate(new_ads, 1):
                    logging.info(f"--- New Ad #{idx} ---")
                    logging.info(f"Title: {ad.get('title', 'N/A')}")
                    logging.info(f"Price: {ad.get('price', 'N/A')}")
                    logging.info(f"URL: {ad.get('url', 'N/A')}")
                    description = ad.get('description', 'N/A')
                    if description != 'N/A' and len(description) > 200:
                        description = description[:200] + "..."
                    logging.info(f"Description: {description}")
                    if ad.get('seller'):
                        logging.info(f"Seller: {ad['seller']}")
                    logging.info("---")

                logging.info(f"Sending notifications for {len(new_ads)} new ads...")
                for idx, ad in enumerate(new_ads, 1):
                    try:
                        message_subject, message_body = Messenger.generate_single_ad_notification(ad)
                        Messenger.send_telegram_message(message_subject, message_body)
                        
                        images = ad.get('images')
                        if images:
                            Messenger.send_telegram_photos(images)
                            logging.info(f"Photos sent for ad #{idx}")
                            
                        logging.info(f"Notification sent for ad #{idx}: {ad.get('title', 'N/A')[:50]}")
                    except Exception as e:
                        logging.error(f"Failed to send notification for ad #{idx}: {e}")
                logging.info(f"All notifications sent ({len(new_ads)} total).")

                logging.info("Updating database with new URLs...")
                processed_urls = {ad.get('url') for ad in new_ads if ad.get('url')}
                for url in processed_urls:
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
