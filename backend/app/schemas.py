from pydantic import BaseModel, Field, EmailStr
from typing import Optional

class UserBase(BaseModel):
    user_id: str = Field(..., description="Unique ID for the user")
    email: EmailStr
    full_name: str
    is_active: bool = True

class UserCreate(UserBase):
    pass

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    is_active: Optional[bool] = None

class UserInDB(UserBase):
    created_at: str
    updated_at: str
