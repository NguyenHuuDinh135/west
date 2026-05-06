import pytest
import pytest_asyncio
from httpx import AsyncClient
from app.main import app
from app.cache import cache
from app.config import settings
import fakeredis.aioredis
from moto import mock_aws
import aioboto3
import os

# Set dummy AWS credentials for moto
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ap-southeast-1"

@pytest_asyncio.fixture
async def mock_redis():
    # Patch the cache.redis with fakeredis
    cache.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield cache.redis
    await cache.redis.flushall()

@pytest_asyncio.fixture
async def mock_dynamo():
    with mock_aws():
        session = aioboto3.Session()
        async with session.resource("dynamodb", region_name=settings.AWS_REGION) as dynamo:
            await dynamo.create_table(
                TableName=settings.DYNAMODB_TABLE_NAME,
                KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST"
            )
            yield dynamo

@pytest_asyncio.fixture
async def client(mock_redis, mock_dynamo):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

@pytest.mark.asyncio
async def test_create_user(client):
    response = await client.post("/users", json={
        "user_id": "user123",
        "email": "test@example.com",
        "full_name": "Test User"
    })
    assert response.status_code == 201
    data = response.json()
    assert data["user_id"] == "user123"
    assert "created_at" in data

@pytest.mark.asyncio
async def test_get_user_cache_hit(client, mock_redis):
    # Manually set data in redis
    await mock_redis.set("user:user123", '{"user_id": "user123", "email": "test@example.com", "full_name": "Cached User", "is_active": true, "created_at": "now", "updated_at": "now"}')
    
    response = await client.get("/users/user123")
    assert response.status_code == 200
    assert response.json()["full_name"] == "Cached User"

@pytest.mark.asyncio
async def test_get_user_cache_miss(client, mock_redis, mock_dynamo):
    # User in Dynamo but not in Redis
    await client.post("/users", json={
        "user_id": "user456",
        "email": "miss@example.com",
        "full_name": "Dynamo User"
    })
    
    # Verify not in redis yet
    assert await mock_redis.get("user:user456") is None
    
    response = await client.get("/users/user456")
    assert response.status_code == 200
    assert response.json()["full_name"] == "Dynamo User"
    
    # Verify now in redis
    assert await mock_redis.get("user:user456") is not None

@pytest.mark.asyncio
async def test_user_not_found(client):
    response = await client.get("/users/nonexistent")
    assert response.status_code == 404
