"""Schemas de usuarios del portal."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.validators import MIN_PASSWORD_LENGTH, normalize_email


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    role: str  # superadmin | operator | auditor | client
    customer_id: int | None = None  # requerido si role == client

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)


class UserActiveUpdate(BaseModel):
    is_active: bool


class UserPasswordUpdate(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    customer_id: int | None = None
    is_active: bool
    created_at: dt.datetime
    last_login: dt.datetime | None = None
    deleted_at: dt.datetime | None = None
