import json
import os
import requests
import logging
import re
from dotenv import load_dotenv
from utils import normalize_text
import time

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


class Messenger():
    """Class used to group the notification sending methods."""

    @staticmethod
    def generate_ad_string(index: int, ad: dict) -> str:
        """
        Generates the text body of an advertisement.

        Args:
            index (int): index of the ad in the list.
            ad (dict): A dictionary containing details about
            the ad - title, description, price and URL.

        Returns:
            str: The contents of an ad as a string.
        """
        title = normalize_text(ad["title"]).strip()
        description = normalize_text(ad["description"]).strip()[:150]
        price = normalize_text(ad["price"]).strip()
        url = ad["url"]
        return f"{index}. {title} ({price})\n{description}...\n{url}\n\n"

    @staticmethod
    def generate_single_ad_notification(ad: dict) -> tuple[str, str]:
        """
        Generates a formatted notification for a single ad.

        Args:
            ad (dict): A dictionary containing details about the ad.

        Returns:
            tuple[str, str]: A tuple containing (subject, message_body)
        """
        # Don't normalize text - Telegram supports Unicode perfectly fine
        # Just clean up whitespace and preserve original characters
        title = str(ad.get("title", "N/A")).strip()
        price = str(ad.get("price", "N/A")).strip()
        url = ad.get("url", "N/A")
        description = str(ad.get("description", "N/A")).strip()
        seller = str(ad.get("seller", "")).strip() if ad.get("seller") else None
        
        # Clean up excessive whitespace in description
        if description != "N/A":
            description = re.sub(r'\s+', ' ', description)  # Replace multiple spaces with single space
            description = re.sub(r'\n\s*\n', '\n\n', description)  # Clean up excessive newlines
        
        # Truncate description if too long (keep it reasonable for Telegram)
        max_desc_length = 400
        if description != "N/A" and len(description) > max_desc_length:
            description = description[:max_desc_length] + "..."
        
        # Create a nicely formatted message
        message_lines = [
            "",
            f"📌 {title}",
            f"💰 {price}",
            ""
        ]
        
        if seller:
            message_lines.append(f"👤 Seller: {seller}")
            message_lines.append("")
        
        if description != "N/A":
            message_lines.append("📝 Description:")
            message_lines.append(description)
            message_lines.append("")
        
        message_lines.append(f"🔗 {url}")

        images = ad.get("images", [])
        if images:
            message_lines.append("")
            message_lines.append(f"👇 {len(images)} photos attached below 👇")
        
        message_body = "\n".join(message_lines)
        subject = f"New: {title[:50]}{'...' if len(title) > 50 else ''}"
        
        return subject, message_body

    @staticmethod
    def send_telegram_photos(images: list[str]) -> None:
        """
        Send images via Telegram.

        Args:
            images (list[str]): List of image URLs to send.
        """
        if not images:
            return

        endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
        
        # Telegram allows up to 10 media items per group
        # We will slice the list into chunks of 10
        for i in range(0, len(images), 10):
            chunk = images[i:i+10]
            media_group = []
            for img_url in chunk:
                media_group.append({
                    "type": "photo",
                    "media": img_url
                })
            
            try:
                # Using post for complex data
                response = requests.post(endpoint, data={"chat_id": TELEGRAM_CHAT_ID, "media": json.dumps(media_group)})
                
                # If sendMediaGroup fails (e.g. invalid URL), try sending individually
                if not response.ok:
                    logging.warning(f"Failed to send media group: {response.text}. Trying individual photos.")
                    Messenger._send_individual_photos(chunk)
                else:
                    logging.info("Telegram media group sent successfully")
                    
            except requests.exceptions.RequestException as error:
                logging.error(f"Telegram connection error while sending photos: {error}")

    @staticmethod
    def _send_individual_photos(images: list[str]) -> None:
        """Fallback to send photos one by one."""
        endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        for img_url in images:
            try:
                params = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "photo": img_url
                }
                requests.get(endpoint, params=params)
            except Exception as e:
                logging.error(f"Failed to send individual photo {img_url}: {e}")

    @staticmethod
    def send_telegram_message(message_subject: str, message_body: str) -> None:
        """
        Send a message via Telegram. The service accepts messages up to
        4096 characters or less, so the notification will be divided into sections of
        no more than 4000 characters.

        Args:
            message_subject (str): The subject of the notification(s) to be sent.
            message_body (str): Body of the message to be sent.

        Returns:
            None

        Raises:
            requests.exceptions.RequestException: In case an error is generated during the transmission.
        """
        endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        max_length = 4000
        message_batches = []
        current_message = ""
        # Split messages into sections
        chunks = message_body.split("\n\n")
        for chunk in chunks:
            if len(current_message) + len(chunk) <= max_length:
                current_message += chunk + "\n\n"
            else:
                message_batches.append(current_message.strip())
                current_message = chunk + "\n\n"
        message_batches.append(current_message.strip())

        # Send each batch as a separate notification in the same chain
        for i, message_batch in enumerate(message_batches):
            if i == 0:
                message_text = f"{message_subject}\n\n{message_batch}"
            else:
                message_text = message_batch
            params = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message_text
            }
            try:
                response = requests.get(endpoint, params=params)
                response.raise_for_status()
                if response.json()["ok"]:
                    logging.info("Telegram notification sent successfully")
                    time.sleep(1)
                else:
                    logging.error(
                        "Error sending Telegram notification")
            except requests.exceptions.RequestException as error:
                logging.error(f"Telegram connection error: {error}")

    @staticmethod
    def _get_telegram_bot_chats() -> list:
        """
        Helper function to get the details of all the chats in which
        a bot is participating.

        Returns:
            chats (list[dict]): A unique list of dictionaries, each continaing the details of
            a chat. For a private chat, the details are: 'id', 'type', 'first_name', 'last_name',
            'username'. For a group chat, the details are: 'id', 'type', 'title',
            'all_members_are_administrators'. In case of an error, it returns an emtpy list.
        """
        endpoint = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        try:
            response = requests.get(endpoint)
            response.raise_for_status()
            data = response.json()
            results = data.get("result", [])
            chats = []
            for result in results:
                chat = result.get("message", {}).get("chat")
                if chat not in chats:
                    chats.append(chat)
            return chats
        except requests.exceptions.RequestException as error:
            logging.error(f"Error getting Telegram bot chat data: {error}")
            return []
