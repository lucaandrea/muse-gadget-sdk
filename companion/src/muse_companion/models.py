from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Button(StrictModel):
    id: str = Field(max_length=64)
    label: str = Field(max_length=24)
    action: Literal["done", "snooze", "approve", "reject", "open", "next", "dismiss"]


class Card(StrictModel):
    id: str = Field(max_length=64)
    kind: Literal["answer", "memory", "reminder", "approval", "task", "translation", "lesson", "briefing", "notification"]
    title: str = Field(max_length=80)
    body: str = Field(max_length=1600)
    source: str = Field(default="", max_length=200)
    status: str = Field(default="", max_length=32)
    buttons: list[Button] = Field(default_factory=list, max_length=3)


class MemoryIn(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    source: str = Field(default="typed", max_length=120)


class ReminderIn(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    due_at: datetime
    arrival_ssid: str = Field(default="", max_length=32)

    @field_validator("due_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Include a timezone offset")
        return value


class ChatIn(StrictModel):
    text: str = Field(min_length=1, max_length=12000)
    operation_id: str = Field(min_length=8, max_length=96)


class ActionIn(StrictModel):
    action: Literal["done", "snooze", "approve", "reject", "dismiss"]
    operation_id: str = Field(min_length=8, max_length=96)
    minutes: int = Field(default=10, ge=1, le=10080)


class NotificationIn(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(max_length=4000)
    sender: str = Field(default="", max_length=200)
    source_id: str = Field(min_length=1, max_length=120)
    priority: Literal["normal", "important"] = "normal"

