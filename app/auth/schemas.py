"""Data contracts for the auth domain: custom types, requests, responses."""

from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    # Supabase's default minimum password length is 8
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def must_be_gmail(cls, value: EmailStr) -> EmailStr:
        if not value.lower().endswith("@gmail.com"):
            raise ValueError("registration is restricted to Gmail addresses")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    id: str
    email: str
    name: Optional[str] = None


class RegisterResponse(BaseModel):
    user: UserOut
    # None only when email confirmation is enabled on the Supabase
    # project (we set it up disabled; see docs/supabase-setup.md)
    access_token: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    user: UserOut
