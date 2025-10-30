
import os
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from os import environ
from requests import get as rget

# Logger setup
LOG_FILE = "bot_log.txt"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("sharing_bot")
                
# Suppress Pyrogram logs except for errors
logging.getLogger("pyrogram").setLevel(logging.ERROR)

CONFIG_FILE_URL = environ.get('CONFIG_FILE_URL')
try:
    if len(CONFIG_FILE_URL) == 0:
        raise TypeError
    try:
        res = rget(CONFIG_FILE_URL)
        if res.status_code == 200:
            with open('config.env', 'wb+') as f:
                f.write(res.content)
        else:
            logger.error(f"Failed to download config.env {res.status_code}")
    except Exception as e:
        logger.info(f"CONFIG_FILE_URL: {e}")
except:
    pass

load_dotenv('config.env', override=True)

#TELEGRAM API
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

OWNER_ID = int(os.getenv('OWNER_ID'))
BOT_USERNAME = os.getenv('BOT_USERNAME')
UPDATE_CHANNEL_ID = int(os.getenv('UPDATE_CHANNEL_ID', 0))
UPDATE_CHANNEL_ID2 = int(os.getenv('UPDATE_CHANNEL_ID2', 0))
TMDB_CHANNEL_ID = [int(x) for x in os.getenv('TMDB_CHANNEL_ID', '').replace(' ', '').split(',') if x]
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID'))
BACKUP_CHANNEL = os.getenv('BACKUP_CHANNEL', '')
SEND_UPDATES = os.getenv('SEND_UPDATES', 'True').lower() in ('true', '1', 't')

MY_DOMAIN = os.getenv('MY_DOMAIN')
CF_DOMAIN = os.getenv('CF_DOMAIN')

TOKEN_VALIDITY_SECONDS = 24 * 60 * 60  # 24 hours

MONGO_URI = os.getenv("MONGO_URI")

TMDB_API_KEY = os.getenv('TMDB_API_KEY')

#SHORTERNER API
URLSHORTX_API_TOKEN = os.getenv('URLSHORTX_API_TOKEN')
SHORTERNER_URL = os.getenv('SHORTERNER_URL')

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", 0))
REDIS_USERNAME = os.getenv("REDIS_USERNAME")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
MAX_FILES_PER_SESSION = int(os.getenv("MAX_FILES_PER_SESSION", "10"))
