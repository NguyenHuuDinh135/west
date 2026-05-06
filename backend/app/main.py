import os
import json
import logging
import asyncio
from decimal import Decimal
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

import boto3
import redis.asyncio as redis
from botocore.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("product-api")

# Environment variables
TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME")
REDIS_HOST = os.environ.get("REDIS_HOST")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD")
AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "300"))

# AWS Clients
boto_cfg = Config(
    region_name=AWS_REGION,
    retries={"max_attempts": 3, "mode": "adaptive"},
    max_pool_connections=50,
)

# DynamoDB Resource
dynamodb = boto3.resource("dynamodb", config=boto_cfg)
table = dynamodb.Table(TABLE_NAME)

# Redis client
redis_client: Optional[redis.Redis] = None

# Singleflight protection against cache stampede
inflight: Dict[str, asyncio.Future] = {}
inflight_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    logger.info(f"Connecting to Redis at {REDIS_HOST}")
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=6379,
        password=REDIS_PASSWORD,
        ssl=True,
        ssl_cert_reqs=None,
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
        retry_on_timeout=True,
    )
    try:
        await redis_client.ping()
        logger.info("Successfully connected to Redis")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
    
    yield
    
    if redis_client:
        await redis_client.aclose()
        logger.info("Redis connection closed")

app = FastAPI(title="Product API", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Product(BaseModel):
    category: str
    sku: str
    name: str
    price: float
    stock: int

def get_cache_key(category: str, sku: str) -> str:
    return f"product:{category}:{sku}"

async def _fetch_from_dynamodb(category: str, sku: str) -> Optional[Dict[str, Any]]:
    """Helper to fetch from DynamoDB using Lab Guide schema (pk/sk)."""
    try:
        # Lab schema: pk=PRODUCT#category, sk=SKU#sku
        pk = f"PRODUCT#{category}"
        sk = f"SKU#{sku}"
        
        # We run this in a thread to keep FastAPI async loop free
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, 
            lambda: table.get_item(Key={"pk": pk, "sk": sk})
        )
        
        item = response.get("Item")
        if not item:
            return None
            
        return {
            "category": category,
            "sku": sku,
            "name": item.get("name"),
            "price": float(item.get("price", 0)),
            "stock": int(item.get("stock", 0))
        }
    except Exception as e:
        logger.error(f"DynamoDB fetch error: {e}")
        return None

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.get("/readyz")
async def readyz():
    """Check connectivity to downstream services."""
    try:
        # Check Redis
        if redis_client:
            await redis_client.ping()
        else:
            raise Exception("Redis client not initialized")
        
        # Check DynamoDB (simple describe table)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: table.load())
        
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Ready check failed: {e}")
        raise HTTPException(status_code=503, detail=str(e))

@app.get("/products/{category}/{sku}")
async def get_product(category: str, sku: str):
    key = get_cache_key(category, sku)
    
    # 1. Try Cache
    try:
        cached_data = await redis_client.get(key)
        if cached_data:
            logger.info(f"Cache HIT for {key}")
            return {"source": "cache", **json.loads(cached_data)}
    except Exception as e:
        logger.warning(f"Cache lookup failed: {e}")

    # 2. Cache MISS - Use Singleflight to prevent stampede
    async with inflight_lock:
        if key in inflight:
            # Wait for the existing request to finish
            future = inflight[key]
        else:
            # This is the first request, create a future and start fetching
            future = asyncio.get_event_loop().create_future()
            inflight[key] = future
            asyncio.create_task(resolve_miss(category, sku, key, future))

    result = await future
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return {"source": "dynamodb", **result}

async def resolve_miss(category: str, sku: str, key: str, future: asyncio.Future):
    """Worker function to fetch from DB and update cache."""
    try:
        logger.info(f"Cache MISS for {key}, fetching from DynamoDB")
        item = await _fetch_from_dynamodb(category, sku)
        
        if item:
            # Update cache
            try:
                await redis_client.setex(key, CACHE_TTL, json.dumps(item))
            except Exception as e:
                logger.error(f"Failed to update cache: {e}")
        
        future.set_result(item)
    except Exception as e:
        future.set_exception(e)
    finally:
        async with inflight_lock:
            inflight.pop(key, None)

@app.put("/products/{category}/{sku}")
async def upsert_product(category: str, sku: str, product: Product):
    """Update or create a product and invalidate cache."""
    try:
        pk = f"PRODUCT#{category}"
        sk = f"SKU#{sku}"
        
        item = {
            "pk": pk,
            "sk": sk,
            "name": product.name,
            "price": Decimal(str(product.price)),
            "stock": product.stock,
            "gsi1pk": "STATUS#active",
            "gsi1sk": f"PRICE#{int(product.price):010d}"
        }
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: table.put_item(Item=item))
        
        # Invalidate Cache (Cache-aside)
        await redis_client.delete(get_cache_key(category, sku))
        
        return {"status": "success", "message": "Product updated and cache invalidated"}
    except Exception as e:
        logger.error(f"Failed to upsert product: {e}")
        raise HTTPException(status_code=500, detail=str(e))
