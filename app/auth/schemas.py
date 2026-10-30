"""Data contracts for the auth domain: custom types, requests, responses."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Role(str, Enum):
    """What a Profile is for in the product (ADR-0005).

    Exactly one per Profile, fixed at registration. Stored lowercase in
    profiles.role and validated there by a CHECK constraint.
    """

    mother = "mother"
    companion = "companion"
    professional = "professional"


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    # Supabase's default minimum password length is 8
    password: str = Field(min_length=8, max_length=72)
    # Required: a Profile without a Role has no meaning here (ADR-0005)
    role: Role

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
    # Never null for a real Profile (ADR-0005: exactly one Role). Kept
    # Optional only so a pre-migration DB surfaces as null instead of a
    # 500 — the guide's troubleshooting table covers that state.
    role: Optional[Role] = None


class RegisterResponse(BaseModel):
    user: UserOut
    # None only when email confirmation is enabled on the Supabase
    # project (we set it up disabled; see docs/supabase-setup.md)
    access_token: Optional[str] = None


class LoginResponse(BaseModel):
    access_token: str
    user: UserOut


class UpdateMeRequest(BaseModel):
    """The only editable Profile field is the name.

    extra="forbid" is load-bearing: `role` is immutable, and silently
    ignoring it would read as "it worked" to the caller (ADR-0005).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)


class ChangePasswordRequest(BaseModel):
    # Verified by attempting a login; Supabase does no old-password
    # check of its own on PUT /auth/v1/user.
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=72)
