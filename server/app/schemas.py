from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Platform = Literal["qq", "weixin", "wecom"]
TaskStatus = Literal["todo", "doing", "done"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=5, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=5, max_length=128)
    staff_id: int
    role: Literal["admin", "member"] = "member"

    @field_validator("username", mode="before")
    @classmethod
    def trim_username(cls, value):
        return value.strip() if isinstance(value, str) else value


class UserProfile(BaseModel):
    id: int
    username: str
    display_name: str
    staff_id: int
    role: Literal["admin", "member"]


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserProfile


class StaffChannelCreate(BaseModel):
    platform: Platform
    recipient_id: str = Field(min_length=1, max_length=160)
    enabled: bool = True

    @field_validator("recipient_id", mode="before")
    @classmethod
    def trim_recipient(cls, value):
        return value.strip() if isinstance(value, str) else value


class StaffChannelRead(StaffChannelCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class StaffCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    channels: list[StaffChannelCreate] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class StaffUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class StaffRead(BaseModel):
    id: int
    name: str
    enabled: bool
    channels: list[StaffChannelRead]
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    content: str = ""
    assignee_id: int
    creator_name: str = Field(default="客户端用户", max_length=80)
    priority: int = Field(default=2, ge=1, le=3)
    due_at: datetime | None = None
    remind_enabled: bool = True
    remind_interval_minutes: int = Field(default=300, ge=30, le=10080)
    next_remind_at: datetime | None = None
    source_platform: str = Field(default="client", max_length=20)
    source_message_id: str | None = Field(default=None, max_length=160)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = None
    assignee_id: int | None = None
    status: TaskStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    due_at: datetime | None = None
    remind_enabled: bool | None = None
    remind_interval_minutes: int | None = Field(default=None, ge=30, le=10080)
    next_remind_at: datetime | None = None
    operator_name: str = Field(default="客户端用户", max_length=80)


class TaskRead(BaseModel):
    id: int
    task_no: str
    title: str
    content: str
    assignee_id: int
    assignee_name: str
    creator_name: str
    status: TaskStatus
    priority: int
    due_at: datetime | None
    remind_enabled: bool
    remind_interval_minutes: int
    next_remind_at: datetime | None
    last_reminded_at: datetime | None
    reminder_count: int
    completed_at: datetime | None
    source_platform: str
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskList(BaseModel):
    items: list[TaskRead]
    total: int


class ActionRequest(BaseModel):
    operator_name: str = Field(default="客户端用户", max_length=80)


class ChannelCompletionRequest(BaseModel):
    platform: Platform
    recipient_id: str = Field(min_length=1, max_length=160)
    task_no: str | None = Field(default=None, max_length=32)
    all_active: bool = False


class TaskCompletionResult(BaseModel):
    items: list[TaskRead]
    completed_count: int


class TaskEventRead(BaseModel):
    id: int
    task_id: int
    event_type: str
    operator_name: str
    detail: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class SmartChannelUpsert(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    prompt: str = ""
    topic: str = Field(default="", max_length=200)
    server: str = Field(default="", max_length=500)
    schedule: str = Field(default="", max_length=50)
    maxItems: int = Field(default=20, ge=1, le=500)
    includeTerms: list[str] = Field(default_factory=list)
    excludeTerms: list[str] = Field(default_factory=list)
    color: str = Field(default="green", max_length=20)
    state: str = Field(default="draft", max_length=20)

    model_config = ConfigDict(extra="ignore")


class SmartChannelRead(SmartChannelUpsert):
    createdAt: int
    updatedAt: int


class ArticleInput(BaseModel):
    id: str = Field(min_length=1, max_length=191)
    channelId: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    body: str = ""
    timestamp: int = Field(ge=0)
    source: str = Field(default="", max_length=240)
    clickUrl: str | None = None
    tags: list[str] = Field(default_factory=list)
    priority: int = Field(default=3, ge=1, le=5)


class ArticleSyncInput(ArticleInput):
    read: bool = False
    saved: bool = False
    feedback: Literal["useful", "not-useful"] | None = None
    deletedAt: int | None = Field(default=None, ge=0)
    purged: bool = False
    stateUpdatedAt: int = Field(default=0, ge=0)


class ArticleBatchRequest(BaseModel):
    items: list[ArticleInput] = Field(default_factory=list, max_length=1000)


class ArticleSyncRequest(BaseModel):
    items: list[ArticleSyncInput] = Field(default_factory=list, max_length=1000)


class ArticleStatePatch(BaseModel):
    read: bool | None = None
    saved: bool | None = None
    feedback: Literal["useful", "not-useful"] | None = None
    action: Literal["delete", "restore", "permanent"] | None = None
    stateUpdatedAt: int = Field(default=0, ge=0)


class ArticleRead(BaseModel):
    id: str
    channelId: str
    title: str
    body: str
    timestamp: int
    source: str
    clickUrl: str | None
    tags: list[str]
    priority: int
    read: bool
    saved: bool
    feedback: Literal["useful", "not-useful"] | None
    deletedAt: int | None
    purged: bool
    stateUpdatedAt: int


class ArticleList(BaseModel):
    items: list[ArticleRead]
    total: int
