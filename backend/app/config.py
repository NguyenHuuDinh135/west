from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    DYNAMODB_TABLE_NAME: str = "user-profiles"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    AWS_REGION: str = "ap-southeast-1"
    CACHE_TTL: int = 3600  # 1 hour

    class Config:
        env_file = ".env"

settings = Settings()
