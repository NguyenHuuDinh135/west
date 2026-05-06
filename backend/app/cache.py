import json
import aioredis
from .config import settings

class RedisCache:
    def __init__(self):
        self.redis = None

    async def connect(self):
        protocol = "rediss" if settings.REDIS_PASSWORD else "redis"
        url = f"{protocol}://{settings.REDIS_HOST}:{settings.REDIS_PORT}"
        self.redis = await aioredis.from_url(
            url,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
            ssl_cert_reqs=None if settings.REDIS_PASSWORD else "required" # ElastiCache uses self-signed
        )

    async def close(self):
        if self.redis:
            await self.redis.close()
...
    async def get_user(self, user_id: str):
        data = await self.redis.get(f"user:{user_id}")
        return json.loads(data) if data else None

    async def set_user(self, user_id: str, user_data: dict):
        await self.redis.set(
            f"user:{user_id}",
            json.dumps(user_data),
            ex=settings.CACHE_TTL
        )

    async def delete_user(self, user_id: str):
        await self.redis.delete(f"user:{user_id}")

cache = RedisCache()
