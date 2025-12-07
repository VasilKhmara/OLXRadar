import logging
import logging_config
import multiprocessing
import random
import time
from scraper_manager import ScraperOrchestrator
from database_manager import DatabaseManager
from notification_manager import Messenger

# Pause intervals in minutes for different platforms
VINTED_PAUSE_MINUTES = 3
OLX_PAUSE_MINUTES = 1


def get_varied_sleep_time(base_seconds: float) -> float:
    """
    Returns a sleep time with ±10% variation from the base time.
    """
    variation = base_seconds * 0.1  # 10% variation
    return random.uniform(base_seconds - variation, base_seconds + variation)


def run_platform_scraper(platform_name: str, pause_minutes: int) -> None:
    """
    Worker function that runs a scraper for a specific platform in its own process.
    """
    db = DatabaseManager()
    scraper = ScraperOrchestrator(database=db)
    
    # Filter scrapers to only include the target platform
    platform_scrapers = [s for s in scraper.scrapers if s.name.lower() == platform_name.lower()]
    if not platform_scrapers:
        logging.warning(f"No scraper found for platform: {platform_name}")
        return
    
    scraper.scrapers = platform_scrapers
    pause_seconds = pause_minutes * 60
    
    logging.info(f"[{platform_name.upper()}] Starting scraper with {pause_minutes} minute pause interval.")
    
    while True:
        try:
            # Load all target URLs
            target_specs = scraper._load_target_urls()
            if not target_specs:
                logging.info(f"[{platform_name.upper()}] No target URLs configured.")
                sleep_time = get_varied_sleep_time(pause_seconds)
                time.sleep(sleep_time)
                continue
            
            # Filter target URLs to only those supported by this platform's scraper
            platform_scraper = platform_scrapers[0]
            filtered_specs = [spec for spec in target_specs if platform_scraper.supports(spec.url)]
            
            if not filtered_specs:
                logging.info(f"[{platform_name.upper()}] No URLs for this platform. Sleeping for {pause_minutes} minutes...")
                sleep_time = get_varied_sleep_time(pause_seconds)
                time.sleep(sleep_time)
                continue
            
            # Collect new ads for this platform
            new_ads = scraper.collect_new_ads(target_urls=[spec.url for spec in filtered_specs])
            
            if not new_ads:
                logging.info(f"[{platform_name.upper()}] No new ads to process.")
            else:
                logging.info(f"[{platform_name.upper()}] Successfully scraped details for {len(new_ads)} ads.")
                
                for idx, ad in enumerate(new_ads, 1):
                    logging.info(f"[{platform_name.upper()}] --- New Ad #{idx} ---")
                    logging.info(f"[{platform_name.upper()}] Title: {ad.get('title', 'N/A')}")
                    logging.info(f"[{platform_name.upper()}] Price: {ad.get('price', 'N/A')}")
                    logging.info(f"[{platform_name.upper()}] URL: {ad.get('url', 'N/A')}")
                    description = ad.get('description', 'N/A')
                    if description != 'N/A' and len(description) > 200:
                        description = description[:200] + "..."
                    logging.info(f"[{platform_name.upper()}] Description: {description}")
                    if ad.get('seller'):
                        logging.info(f"[{platform_name.upper()}] Seller: {ad['seller']}")
                    logging.info(f"[{platform_name.upper()}] ---")
            
                logging.info(f"[{platform_name.upper()}] Sending notifications for {len(new_ads)} new ads...")
                for idx, ad in enumerate(new_ads, 1):
                    try:
                        message_subject, message_body = Messenger.generate_single_ad_notification(ad)
                        Messenger.send_telegram_message(message_subject, message_body)
                        
                        images = ad.get('images')
                        if images:
                            Messenger.send_telegram_photos(images)
                            logging.info(f"[{platform_name.upper()}] Photos sent for ad #{idx}")
                            
                        logging.info(f"[{platform_name.upper()}] Notification sent for ad #{idx}: {ad.get('title', 'N/A')[:50]}")
                    except Exception as e:
                        logging.error(f"[{platform_name.upper()}] Failed to send notification for ad #{idx}: {e}")
                logging.info(f"[{platform_name.upper()}] All notifications sent ({len(new_ads)} total).")
       
                logging.info(f"[{platform_name.upper()}] Updating database with new URLs...")
                processed_urls = {ad.get('url') for ad in new_ads if ad.get('url')}
                for url in processed_urls:
                    db.add_url(url)
                logging.debug(f"[{platform_name.upper()}] Database updated.")
            
            logging.info(f"[{platform_name.upper()}] Cycle completed. Sleeping for {pause_minutes} minutes...")
            sleep_time = get_varied_sleep_time(pause_seconds)
            time.sleep(sleep_time)
            
        except KeyboardInterrupt:
            logging.info(f"[{platform_name.upper()}] Stopping scraper...")
            break
        except Exception as e:
            logging.error(f"[{platform_name.upper()}] An unexpected error occurred: {e}")
            logging.info(f"[{platform_name.upper()}] Retrying in {pause_minutes} minutes...")
            sleep_time = get_varied_sleep_time(pause_seconds)
            time.sleep(sleep_time)


def main() -> None:
    """
    Main function. Starts separate processes for each platform scraper.
    """
    logging.info(f"Starting OLX Radar. Pause intervals: OLX={OLX_PAUSE_MINUTES}min, Vinted={VINTED_PAUSE_MINUTES}min.")
    
    # Create processes for each platform
    processes = []
    
    # Start OLX scraper process
    olx_process = multiprocessing.Process(
        target=run_platform_scraper,
        args=("olx", OLX_PAUSE_MINUTES),
        name="OLX-Scraper"
    )
    olx_process.start()
    processes.append(olx_process)
    logging.info("Started OLX scraper process.")
    
    # Start Vinted scraper process
    vinted_process = multiprocessing.Process(
        target=run_platform_scraper,
        args=("vinted", VINTED_PAUSE_MINUTES),
        name="Vinted-Scraper"
    )
    vinted_process.start()
    processes.append(vinted_process)
    logging.info("Started Vinted scraper process.")
    
    try:
        # Wait for all processes to complete (they run indefinitely)
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        logging.info("Stopping all scraper processes...")
        for process in processes:
            process.terminate()
            process.join(timeout=5)
        logging.info("All processes stopped.")


if __name__ == "__main__":
    main()
