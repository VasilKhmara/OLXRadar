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
   1. Search for a product on [www.olx.ro](https://www.olx.ro/).
   2. Copy the URL of the search results page.
   3. Add the URL to `target_urls.txt`, located in the project directory. Add one URL per line.

![How to get a search url](https://i.imgur.com/9tEANnp.png)
