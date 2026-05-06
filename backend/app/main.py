from fastapi import FastAPI, HTTPException, status
from contextlib import asynccontextmanager
import asyncio
from typing import Dict
from .schemas import UserCreate, UserUpdate, UserInDB
from .crud import db
from .cache import cache

# Singleflight protection
_inflight_requests: Dict[str, asyncio.Future] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await cache.connect()
    await db.connect()
    yield
    await db.close()
    await cache.close()

app = FastAPI(title="User Profile API", lifespan=lifespan)

@app.get("/healthz")
async def health_check():
    return {"status": "healthy"}

@app.post("/users", response_model=UserInDB, status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate):
    existing = await db.get_user(user.user_id)
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    
    new_user = await db.create_user(user)
    return new_user

@app.get("/users/{user_id}", response_model=UserInDB)
async def get_user(user_id: str):
    # Cache-aside with singleflight
    user = await cache.get_user(user_id)
    if user:
        return user

    if user_id in _inflight_requests:
        return await _inflight_requests[user_id]

    future = asyncio.Future()
    _inflight_requests[user_id] = future
    
    try:
        user = await db.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        await cache.set_user(user_id, user)
        future.set_result(user)
        return user
    except Exception as e:
        future.set_exception(e)
        raise e
    finally:
        _inflight_requests.pop(user_id, None)

@app.patch("/users/{user_id}", response_model=UserInDB)
async def update_user(user_id: str, user_update: UserUpdate):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    updated_user = await db.update_user(user_id, user_update)
    await cache.delete_user(user_id)
    return updated_user

@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str):
    user = await db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await db.delete_user(user_id)
    await cache.delete_user(user_id)
    return None
