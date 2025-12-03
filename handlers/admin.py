import re
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, Header, status
from db import tmdb_col, files_col, genres_col, stars_col, directors_col, allowed_channels_col
from utility import is_user_authorized, build_search_pipeline, safe_api_call, upload_to_imgbb
from config import OWNER_ID, SEND_UPDATES, UPDATE_CHANNEL_ID
from app import bot
from cache import invalidate_cache
from bson.objectid import ObjectId
from pyrogram import enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from tmdb import get_info, upsert_tmdb_info, format_tmdb_info_from_db
from typing import Optional

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization header missing")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization scheme")

    token = parts[1]

    try:
        user_id = int(token)
        if not await is_user_authorized(user_id):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization required — please verify through the bot first.")
        return user_id
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format")

async def get_current_admin(user_id: int = Depends(get_current_user)):
    if user_id != OWNER_ID:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user_id

POSTER_BASE_URL = 'https://image.tmdb.org/t/p/original'

@router.get("/tmdb")
async def get_tmdb_entries(admin_id: int = Depends(get_current_admin), page: int = 1, search: str = None, tmdb_type: str = None):
    page_size = 10
    skip = (page - 1) * page_size
    query = {}
    if search:
        escaped_search = re.escape(search)
        query["title"] = {"$regex": escaped_search, "$options": "i"}
    if tmdb_type:
        query["tmdb_type"] = tmdb_type

    entries = []
    async for entry in tmdb_col.find(query).sort("_id", -1).skip(skip).limit(page_size):
        entries.append({
            "tmdb_id": entry.get("tmdb_id"),
            "title": entry.get("title"),
            "type": entry.get("tmdb_type"),
            "rating": entry.get("rating"),
            "plot": entry.get("plot"),
            "year": entry.get("year"),
            "poster_path": entry.get("poster_path")
        })

    total_entries = await tmdb_col.count_documents(query)
    total_pages = (total_entries + page_size - 1) // page_size
    
    return {
        "entries": entries,
        "total_pages": total_pages,
        "current_page": page
    }

@router.get("/tmdb/{tmdb_id}/tv")
async def get_tv_seasons(tmdb_id: int, admin_id: int = Depends(get_current_admin)):
    entry = await tmdb_col.find_one({"tmdb_id": tmdb_id, "tmdb_type": "tv"})
    if not entry:
        raise HTTPException(status_code=404, detail="TV show not found")
    return {"seasons": entry.get("seasons", [])}

@router.get("/tmdb/{tmdb_id}/tv/season/{season_number}")
async def get_tv_season_episodes(tmdb_id: int, season_number: int, admin_id: int = Depends(get_current_admin)):
    entry = await tmdb_col.find_one({"tmdb_id": tmdb_id, "tmdb_type": "tv"})
    if not entry:
        raise HTTPException(status_code=404, detail="TV show not found")

    for season in entry.get("seasons", []):
        if season.get("season_number") == season_number:
            return {"episodes": season.get("episodes", [])}

    raise HTTPException(status_code=404, detail="Season not found")

@router.get("/channels")
async def get_channels(admin_id: int = Depends(get_current_admin)):
    channels = []
    async for channel in allowed_channels_col.find({}, {"_id": 0, "channel_id": 1, "channel_name": 1}):
        channels.append(channel)
    return channels

@router.get("/files")
async def get_files(admin_id: int = Depends(get_current_admin), page: int = 1, search: str = None, no_tmdb_id: bool = False, channel_id: int = None):
    page_size = 10
    skip = (page - 1) * page_size
    query = {}
    
    if no_tmdb_id:
        query["tmdb_id"] = {"$exists": False}
    
    if channel_id:
        query["channel_id"] = channel_id

    if search:
        sanitized_search = bot.sanitize_query(search)
        pipeline = build_search_pipeline(sanitized_search, query, skip, page_size)
        result = await files_col.aggregate(pipeline).to_list(length=None)
        files_data = result[0]['results'] if result and 'results' in result[0] else []
        total_files = result[0]['totalCount'][0]['total'] if result and 'totalCount' in result[0] and result[0]['totalCount'] else 0
    else:
        files_cursor = files_col.find(query).sort("_id", 1).skip(skip).limit(page_size)
        total_files = await files_col.count_documents(query)
        files_data = await files_cursor.to_list(length=page_size)

    files = []
    for file in files_data:
        files.append({
            "id": str(file.get("_id")),
            "file_name": file.get("file_name"),
            "tmdb_id": file.get("tmdb_id"),
            "poster_url": file.get("poster_url")
        })
        
    total_pages = (total_files + page_size - 1) // page_size
    
    return {
        "files": files,
        "total_pages": total_pages,
        "current_page": page
    }

@router.post("/tmdb/send")
async def send_to_channel(data: dict, admin_id: int = Depends(get_current_admin)):
    tmdb_id = data.get("tmdb_id")
    tmdb_type = data.get("tmdb_type")

    # Fetch entry from the database
    tmdb_document = await tmdb_col.find_one({"tmdb_id": tmdb_id, "tmdb_type": tmdb_type})
    if not tmdb_document:
        raise HTTPException(status_code=404, detail="TMDB entry not found in database")

    # Format the message using data from the database
    caption = await format_tmdb_info_from_db(tmdb_document)
    
    poster_url = f"{POSTER_BASE_URL}{tmdb_document.get('poster_path')}" if tmdb_document.get('poster_path') else None

    if SEND_UPDATES and poster_url:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🎥 Trailer", url=tmdb_document["trailer_url"])]]
        ) if tmdb_document.get("trailer_url") else None

        await safe_api_call(
            lambda: bot.send_photo(
                UPDATE_CHANNEL_ID,
                photo=poster_url,
                caption=caption,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=keyboard
            )
        )
        return {"status": "success"}
    else:
        raise HTTPException(status_code=400, detail="Sending updates is disabled or poster URL is missing")

@router.post("/tmdb/send-all")
async def send_all_to_channel(data: dict = None, admin_id: int = Depends(get_current_admin)):
    if not SEND_UPDATES:
        raise HTTPException(status_code=400, detail="Sending updates is disabled")

    query = {}
    if data:
        restart_tmdb_id = data.get("restart_tmdb_id")
        restart_tmdb_type = data.get("restart_tmdb_type")
        if restart_tmdb_id and restart_tmdb_type:
            last_doc = await tmdb_col.find_one({"tmdb_id": restart_tmdb_id, "tmdb_type": restart_tmdb_type})
            if last_doc:
                query["_id"] = {"$gt": last_doc["_id"]}

    cursor = tmdb_col.find(query).sort("_id", 1)
    async for entry in cursor:
        caption = await format_tmdb_info_from_db(entry)
        poster_url = f"{POSTER_BASE_URL}{entry.get('poster_path')}" if entry.get('poster_path') else None

        if poster_url:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🎥 Trailer", url=entry["trailer_url"])]]
            ) if entry.get("trailer_url") else None

            await safe_api_call(
                lambda: bot.send_photo(
                    UPDATE_CHANNEL_ID,
                    photo=poster_url,
                    caption=caption,
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=keyboard
                )
            )
            await asyncio.sleep(3)
    return {"status": "success"}

@router.post("/tmdb")
async def add_tmdb_entry(data: dict, admin_id: int = Depends(get_current_admin)):
    tmdb_id = data.get("tmdb_id")
    tmdb_type = data.get("tmdb_type")
    file_ids = data.get("file_ids", [])

    try:
        tmdb_id = int(tmdb_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid TMDB ID")

    info = await get_info(tmdb_type, tmdb_id)
    if "message" in info and info["message"].startswith("Error"):
        raise HTTPException(status_code=404, detail=info["message"])
    
    await upsert_tmdb_info(tmdb_id, tmdb_type, info)

    # Update associated files
    if file_ids:
        update_data = {"$set": {"tmdb_id": tmdb_id, "tmdb_type": tmdb_type}}
        if tmdb_type == "tv":
            season_number = data.get("season_number")
            if season_number:
                try:
                    update_data["$set"]["season_number"] = int(season_number)
                except (ValueError, TypeError):
                    pass

        for file_id in file_ids:
            await files_col.update_one({"_id": ObjectId(file_id)}, update_data)

    invalidate_cache()
    return {"status": "success"}

@router.delete("/tmdb/{tmdb_id}/{tmdb_type}")
async def delete_tmdb_entry(tmdb_id: str, tmdb_type: str, admin_id: int = Depends(get_current_admin)):
    # Convert to int if possible, otherwise keep as string
    try:
        tmdb_id_converted = int(tmdb_id)
    except ValueError:
        tmdb_id_converted = tmdb_id
        
    await tmdb_col.delete_one({"tmdb_id": tmdb_id_converted, "tmdb_type": tmdb_type})
    await files_col.update_many({"tmdb_id": tmdb_id_converted, "tmdb_type": tmdb_type}, {"$unset": {"tmdb_id": "", "tmdb_type": ""}})
    invalidate_cache()
    return {"status": "success"}

@router.put("/tmdb/{tmdb_id}/{tmdb_type}")
async def update_tmdb_entry(tmdb_id: str, tmdb_type: str, data: dict, admin_id: int = Depends(get_current_admin)):
    # Convert to int if possible, otherwise keep as string
    try:
        tmdb_id_converted = int(tmdb_id)
    except ValueError:
        tmdb_id_converted = tmdb_id

    rating_str = data.get("rating")
    if rating_str == "":
        rating = None
    else:
        try:
            rating = float(rating_str)
        except (ValueError, TypeError):
            rating = None
            
    update_data = {
        "title": data.get("title"),
        "rating": rating,
        "plot": data.get("plot"),
        "year": data.get("year"),
    }
    if data.get("poster_path") is not None:
        update_data["poster_path"] = data.get("poster_path")

    await tmdb_col.update_one({"tmdb_id": tmdb_id_converted, "tmdb_type": tmdb_type}, {"$set": update_data})
    invalidate_cache()
    return {"status": "success"}

@router.put("/files/{file_id}")
async def update_file_poster(file_id: str, data: dict, admin_id: int = Depends(get_current_admin)):
    poster_url = data.get("poster_url")
    try:
        imgbb_url = await upload_to_imgbb(poster_url)
        await files_col.update_one({"_id": ObjectId(file_id)}, {"$set": {"poster_url": imgbb_url}})
        invalidate_cache()
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.delete("/files/{file_id}")
async def delete_file(file_id: str, admin_id: int = Depends(get_current_admin)):
    await files_col.delete_one({"_id": ObjectId(file_id)})
    invalidate_cache()
    return {"status": "success"}
