import re
import base64
import cache
from fastapi import FastAPI, Request, Depends, HTTPException, status, Header
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import MY_DOMAIN, CF_DOMAIN
from utility import is_user_authorized, get_user_firstname, build_search_pipeline
from db import tmdb_col, files_col, comments_col
from tmdb import POSTER_BASE_URL
from app import bot
from config import TMDB_CHANNEL_ID, OWNER_ID
from datetime import datetime, timezone
from handlers.admin import router as admin_router
from bson.objectid import ObjectId
from pydantic import BaseModel

api = FastAPI()

api.include_router(admin_router)

api.add_middleware(
    CORSMiddleware,
    allow_origins=[f"{CF_DOMAIN}"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SendFileRequest(BaseModel):
    file_id: str

# Dependency to get user_id from Authorization header
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


@api.post("/api/send_file")
async def send_file_to_user(request: SendFileRequest, user_id: int = Depends(get_current_user)):
    try:
        file = await files_col.find_one({"_id": ObjectId(request.file_id)})
        if not file:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        channel_id = file.get("channel_id")
        message_id = file.get("message_id")

        if not channel_id or not message_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="File metadata is incomplete")

        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=channel_id,
            message_id=message_id,
            protect_content=True
        )
        return JSONResponse(content={"message": "File sent successfully"})
    except Exception as e:
        logging.error(f"Failed to send file to user {user_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send file")

@api.get("/")
async def root():
    return JSONResponse({"message": "👋 Hola Amigo!"})

@api.post("/api/authorize")
async def api_authorize(request: Request):
    data = await request.json()
    user_id = data.get("user_id")

    try:
        user_id = int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid User ID format.",
        )

    if not await is_user_authorized(user_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization required — please verify through the bot first.",
        )

    # Instead of setting a cookie, return the user_id as a token
    return JSONResponse(content={"token": str(user_id)})


@api.get("/api/user/me")
async def get_user_me(user_id: int = Depends(get_current_user)):
    first_name = await get_user_firstname(user_id)
    return JSONResponse(content={"first_name": first_name})




@api.get("/api/movies")
async def get_movies(page: int = 1, search: str = None, category: str = None, sort: str = "year", user_id: int = Depends(get_current_user), tmdb_id: int = None, tmdb_type: str = None):
    cache_key = f"movies:{page}:{search}:{category}:{sort}:{tmdb_id}:{tmdb_type}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data
        
    page_size = 10
    skip = (page - 1) * page_size

    query = {}
    if search:
        query["title"] = {"$regex": re.escape(search), "$options": "i"}
    if category:
        query["tmdb_type"] = category
    if tmdb_id and tmdb_type:
        query["tmdb_id"] = tmdb_id
        query["tmdb_type"] = tmdb_type

    sort_order = []
    if sort == "rating":
        sort_order.append(("rating", -1))
        sort_order.append(("_id", -1))
    elif sort == "year":
        sort_order.append(("year", -1))
        sort_order.append(("_id", -1))
    else:  # Default to recent
        sort_order.append(("_id", -1))
    
    movies = await tmdb_col.find(query).sort(sort_order).skip(skip).limit(page_size).to_list(length=page_size)
    total_movies = await tmdb_col.count_documents(query)

    # Convert ObjectId to string
    for movie in movies:
        movie["_id"] = str(movie["_id"])

    data = {
        "movies": movies,
        "total_pages": (total_movies + page_size - 1) // page_size,
        "current_page": page
    }
    cache.set(cache_key, data)
    return data

@api.get("/api/details/{tmdb_id}")
async def get_movie_details(tmdb_id: str, tmdb_type: str, page: int = 1, user_id: int = Depends(get_current_user)):
    cache_key = f"details:{tmdb_id}:{tmdb_type}:{page}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data

    try:
        tmdb_id = int(tmdb_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid TMDB ID")

    page_size = 10
    skip = (page - 1) * page_size

    query = {
        "tmdb_id": tmdb_id,
        "tmdb_type": tmdb_type,
        "file_name": {"$not": {"$regex": r"\.srt$", "$options": "i"}}
    }

    files = await files_col.find(query).skip(skip).limit(page_size).to_list(length=page_size)
    total_files = await files_col.count_documents({"tmdb_id": tmdb_id, "tmdb_type": tmdb_type})

    # Convert ObjectId to string and add stream URL
    for file in files:
        file["_id"] = str(file["_id"])
        file["stream_url"] = f"{MY_DOMAIN}/player/{bot.encode_file_link(file['channel_id'], file['message_id'])}"

    data = {
        "files": files,
        "total_pages": (total_files + page_size - 1) // page_size,
        "current_page": page
    }
    cache.set(cache_key, data)
    return data

@api.get("/api/file/{file_id}")
async def get_file_details(file_id: str, user_id: int = Depends(get_current_user)):
    try:
        file = await files_col.find_one({"_id": ObjectId(file_id)})
        if not file:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

        file["_id"] = str(file["_id"])
        file["stream_url"] = f"{MY_DOMAIN}/player/{bot.encode_file_link(file['channel_id'], file['message_id'])}"
        return file
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file ID")

@api.get("/api/others")
async def get_others(page: int = 1, search: str = None, sort: str = "recent", user_id: int = Depends(get_current_user)):
    cache_key = f"others:{page}:{search}:{sort}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data
        
    page_size = 10
    skip = (page - 1) * page_size

    sort_order = [("_id", -1)] if sort == "recent" else [("_id", 1)]

    if search:
        sanitized_search = bot.sanitize_query(search)
        pipeline = build_search_pipeline(sanitized_search, {"channel_id": {"$nin": TMDB_CHANNEL_ID}}, skip, page_size)
        result = await files_col.aggregate(pipeline).to_list(length=None)
        files = result[0]['results'] if result and 'results' in result[0] else []
        total_files = result[0]['totalCount'][0]['total'] if result and 'totalCount' in result[0] and result[0]['totalCount'] else 0
    else:
        query = {"channel_id": {"$nin": TMDB_CHANNEL_ID}}
        files = await files_col.find(query).sort(sort_order).skip(skip).limit(page_size).to_list(length=page_size)
        total_files = await files_col.count_documents(query)

    for file in files:
        file["_id"] = str(file["_id"])
        file["stream_url"] = f"{MY_DOMAIN}/player/{bot.encode_file_link(file['channel_id'], file['message_id'])}"

    data = {
        "files": files,
        "total_pages": (total_files + page_size - 1) // page_size,
        "current_page": page
    }
    cache.set(cache_key, data)
    return data

@api.post("/api/comments")
async def create_comment(request: Request, user_id: int = Depends(get_current_user)):
    data = await request.json()
    comment_text = data.get("comment")
    user_name = await get_user_firstname(user_id)
    if not comment_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comment text cannot be empty.")

    comment = {
        "user_name": user_name,
        "comment": comment_text,
        "created_at": datetime.now(timezone.utc)
    }
    await comments_col.insert_one(comment)
    return {"message": "Comment added successfully"}

@api.get("/api/comments")
async def get_comments(page: int = 1, user_id: int = Depends(get_current_user)):
    page_size = 5
    skip = (page - 1) * page_size

    comments = []
    async for comment in comments_col.find().sort("_id", -1).skip(skip).limit(page_size):
        comment["_id"] = str(comment["_id"])
        comment["first_name"] = comment["user_name"]
        comments.append(comment)

    total_comments = await comments_col.count_documents({})

    return {
        "comments": comments,
        "total_pages": (total_comments + page_size - 1) // page_size,
        "current_page": page
    }

'''
@api.get("/player/{file_link}")
async def stream_player(file_link: str, request: Request):
    try:
        padding = '=' * (-len(file_link) % 4)
        decoded = base64.urlsafe_b64decode(file_link + padding).decode()
        channel_id, msg_id = map(int, decoded.split("_"))

        # You might want to add authorization checks here

        # Get the stream link from the bot
        # This is a placeholder for the actual logic to get the stream link
        stream_link = await bot.get_stream_link(channel_id, msg_id)

        return RedirectResponse(url=stream_link)

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

'''
