
import redis
import json
from config import REDIS_HOST, REDIS_PORT, REDIS_USERNAME, REDIS_PASSWORD
from cachetools import TTLCache

# In-memory caches
user_file_count = TTLCache(maxsize=1000, ttl=3600)
query_id_map = TTLCache(maxsize=1000, ttl=300)
search_api_cache = TTLCache(maxsize=100, ttl=300)
search_cache = TTLCache(maxsize=100, ttl=300)


if REDIS_HOST:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        username=REDIS_USERNAME,
        password=REDIS_PASSWORD,
    )
else:
    redis_client = None

def get(key):
    if not redis_client:
        return None
    
    value = redis_client.get(key)
    if value:
        return json.loads(value)
    return None

def set(key, value, ttl=300):
    if not redis_client:
        return
        
    redis_client.set(key, json.dumps(value), ex=ttl)
