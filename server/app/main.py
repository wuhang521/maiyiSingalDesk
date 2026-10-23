from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import AuthPrincipal, require_api_key, require_shared_api_key, require_staff_admin
from .auth_routes import router as auth_router
from .articles import router as articles_router
from .video import router as video_router
from .video_projects import router as video_projects_router
from .content import router as content_router
from .members import router as members_router
from .knowledge import router as knowledge_router
from .knowledge_gateway import router as knowledge_gateway_router
from .knowledge_teams import router as knowledge_teams_router
from .knowledge_notifications import router as knowledge_notifications_router
from .knowledge_media import router as knowledge_media_router
from .knowledge_capture import router as knowledge_capture_router
from .knowledge_costs import router as knowledge_costs_router
from .member_service import (
    commit_member, load_member, lock_membership, protect_access, require_assignable, set_member_enabled,
)
from .config import get_settings
from .database import Base, engine, get_db
from .models import NotificationOutbox, Staff, StaffChannel, Task, TaskEvent, utcnow
from .schemas import (
    ActionRequest,
    ChannelCompletionRequest,
    StaffChannelCreate,
    StaffCreate,
    StaffRead,
    StaffUpdate,
    TaskCreate,
    TaskCompletionResult,
    TaskEventRead,
    TaskList,
    TaskRead,
    TaskUpdate,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(articles_router)
app.include_router(video_router)
app.include_router(video_projects_router)
app.include_router(content_router)
app.include_router(members_router)
app.include_router(knowledge_router)
app.include_router(knowledge_gateway_router)
app.include_router(knowledge_teams_router)
app.include_router(knowledge_notifications_router)
app.include_router(knowledge_media_router)
app.include_router(knowledge_capture_router)
app.include_router(knowledge_costs_router)


@app.middleware("http")
async def private_knowledge_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/v1/knowledge"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response

secured = [Depends(require_api_key)]


def get_staff(db: Session, staff_id: int) -> Staff:
    staff = db.scalar(
        select(Staff).options(selectinload(Staff.channels)).where(Staff.id == staff_id)
    )
    if not staff:
        raise HTTPException(status_code=404, detail="人员不存在")
    return staff


def get_task(db: Session, task_id: int, include_deleted: bool = False) -> Task:
    query = select(Task).options(selectinload(Task.assignee)).where(Task.id == task_id)
    if not include_deleted:
        query = query.where(Task.deleted_at.is_(None))
    task = db.scalar(query)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def completion_operator(principal: AuthPrincipal, assignee_id: int) -> str:
    if principal.user is None or principal.user.staff_id != assignee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有任务负责人本人可以完成该任务",
        )
    return principal.user.staff.name


def channel_staff(db: Session, platform: str, recipient_id: str) -> Staff:
    channel = db.scalar(
        select(StaffChannel)
        .options(selectinload(StaffChannel.staff))
        .where(
            StaffChannel.platform == platform,
            StaffChannel.recipient_id == recipient_id.strip(),
            StaffChannel.enabled.is_(True),
        )
    )
    if not channel or not channel.staff.enabled or channel.staff.removed_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前消息账号没有绑定负责人",
        )
    return channel.staff


def task_read(task: Task) -> TaskRead:
    return TaskRead(
        id=task.id,
        task_no=task.task_no or "",
        title=task.title,
        content=task.content,
        assignee_id=task.assignee_id,
        assignee_name=task.assignee.name,
        creator_name=task.creator_name,
        status=task.status,
        priority=task.priority,
        due_at=task.due_at,
        remind_enabled=task.remind_enabled,
        remind_interval_minutes=task.remind_interval_minutes,
        next_remind_at=task.next_remind_at,
        last_reminded_at=task.last_reminded_at,
        reminder_count=task.reminder_count,
        completed_at=task.completed_at,
        source_platform=task.source_platform,
        deleted_at=task.deleted_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def add_event(db: Session, task: Task, event_type: str, operator: str, detail: str = "") -> None:
    db.add(
        TaskEvent(
            task_id=task.id,
            event_type=event_type,
            operator_name=operator,
            detail=detail,
        )
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/staff", response_model=list[StaffRead], dependencies=secured)
def list_staff(
    include_disabled: bool = False,
    db: Session = Depends(get_db),
) -> list[Staff]:
    query = select(Staff).options(selectinload(Staff.channels)).where(Staff.removed_at.is_(None)).order_by(Staff.name)
    if not include_disabled:
        query = query.where(Staff.enabled.is_(True))
    return list(db.scalars(query).all())


@app.post(
    "/api/v1/staff",
    response_model=StaffRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_staff_admin)],
)
def create_staff(payload: StaffCreate, db: Session = Depends(get_db)) -> Staff:
    if db.scalar(select(Staff.id).where(Staff.name == payload.name.strip())):
        raise HTTPException(status_code=409, detail="人员名称已存在")
    staff = Staff(name=payload.name.strip(), enabled=payload.enabled)
    staff.channels = [
        StaffChannel(
            platform=channel.platform,
            recipient_id=channel.recipient_id.strip(),
            enabled=channel.enabled,
        )
        for channel in payload.channels
    ]
    db.add(staff)
    commit_member(db)
    return get_staff(db, staff.id)


@app.patch("/api/v1/staff/{staff_id}", response_model=StaffRead)
def update_staff(
    staff_id: int,
    payload: StaffUpdate,
    principal: AuthPrincipal = Depends(require_staff_admin),
    db: Session = Depends(get_db),
) -> Staff:
    lock_membership(db, principal)
    staff = load_member(db, staff_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            raise HTTPException(422, "姓名和启用状态不能为空")
        if field == "enabled":
            if not value:
                protect_access(db, staff, principal)
            set_member_enabled(db, staff, value)
        else:
            staff.name = value.strip()
    commit_member(db)
    return get_staff(db, staff_id)


@app.post(
    "/api/v1/staff/{staff_id}/channels",
    response_model=StaffRead,
    dependencies=[Depends(require_staff_admin)],
)
def add_staff_channel(
    staff_id: int,
    payload: StaffChannelCreate,
    db: Session = Depends(get_db),
) -> Staff:
    staff = load_member(db, staff_id)
    staff.channels.append(
        StaffChannel(
            platform=payload.platform,
            recipient_id=payload.recipient_id.strip(),
            enabled=payload.enabled,
        )
    )
    commit_member(db)
    return get_staff(db, staff_id)


@app.delete(
    "/api/v1/staff/{staff_id}/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_staff_admin)],
)
def delete_staff_channel(
    staff_id: int,
    channel_id: int,
    db: Session = Depends(get_db),
) -> None:
    channel = db.scalar(
        select(StaffChannel).where(
            StaffChannel.id == channel_id,
            StaffChannel.staff_id == staff_id,
        )
    )
    if not channel:
        raise HTTPException(status_code=404, detail="渠道绑定不存在")
    db.delete(channel)
    db.commit()


@app.get("/api/v1/tasks", response_model=TaskList, dependencies=secured)
def list_tasks(
    state: str = Query(default="active", pattern="^(active|done|deleted|all)$"),
    assignee_id: int | None = None,
    search: str = "",
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> TaskList:
    filters = []
    if state == "active":
        filters.extend([Task.deleted_at.is_(None), Task.status != "done"])
    elif state == "done":
        filters.extend([Task.deleted_at.is_(None), Task.status == "done"])
    elif state == "deleted":
        filters.append(Task.deleted_at.is_not(None))
    if assignee_id:
        filters.append(Task.assignee_id == assignee_id)
    if search.strip():
        keyword = f"%{search.strip()}%"
        filters.append(
            or_(
                Task.task_no.like(keyword),
                Task.title.like(keyword),
                Task.content.like(keyword),
            )
        )

    count_query = select(func.count(Task.id)).where(*filters)
    query = (
        select(Task)
        .options(selectinload(Task.assignee))
        .where(*filters)
        .order_by(Task.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return TaskList(
        items=[task_read(item) for item in db.scalars(query).all()],
        total=db.scalar(count_query) or 0,
    )


@app.post(
    "/api/v1/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=secured,
)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    source_platform = payload.source_platform.strip().lower() or "client"
    raw_source_message_id = (payload.source_message_id or "").strip()
    source_message_id = (
        hashlib.sha256(raw_source_message_id.encode("utf-8")).hexdigest()
        if raw_source_message_id
        else None
    )
    if source_message_id:
        existing = db.scalar(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(
                Task.source_platform == source_platform,
                Task.source_message_id == source_message_id,
            )
        )
        if existing:
            return task_read(existing)
    assignee = get_staff(db, payload.assignee_id)
    require_assignable(assignee)
    values = payload.model_dump()
    values["source_platform"] = source_platform
    values["source_message_id"] = source_message_id
    if values["next_remind_at"] is None and values["remind_enabled"]:
        values["next_remind_at"] = utcnow() + timedelta(
            minutes=values["remind_interval_minutes"]
        )
    task = Task(**values)
    try:
        db.add(task)
        db.flush()
        task.task_no = f"T{task.id}"
        add_event(db, task, "created", task.creator_name, "创建任务")
        wecom_tasks = None
        for channel in assignee.channels:
            if not channel.enabled:
                continue
            tasks_for_notification = [task]
            if channel.platform.lower() == "wecom":
                if wecom_tasks is None:
                    wecom_tasks = list(
                        db.scalars(
                            select(Task).where(
                                Task.assignee_id == assignee.id,
                                Task.deleted_at.is_(None),
                                Task.status != "done",
                            )
                        ).all()
                    )
                tasks_for_notification = wecom_tasks
            notification = {
                "event": "task.created",
                "assignee": assignee.name,
                "creator_name": task.creator_name,
                "platform": channel.platform,
                "recipient_id": channel.recipient_id,
                "tasks": [
                    {
                        "task_id": item.id,
                        "task_no": item.task_no,
                        "title": item.title,
                        "content": item.content,
                        "priority": item.priority,
                        "status": item.status,
                        "reminder_count": 0,
                        "due_at": item.due_at.isoformat() if item.due_at else None,
                    }
                    for item in tasks_for_notification
                ],
            }
            db.add(
                NotificationOutbox(
                    task_id=task.id,
                    platform=channel.platform,
                    recipient_id=channel.recipient_id,
                    dedupe_key=f"created:{task.id}:{channel.id}",
                    payload=json.dumps(notification, ensure_ascii=False),
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        if not source_message_id:
            raise
        existing = db.scalar(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(
                Task.source_platform == source_platform,
                Task.source_message_id == source_message_id,
            )
        )
        if not existing:
            raise
        return task_read(existing)
    return task_read(get_task(db, task.id))


@app.get(
    "/api/v1/tasks/by-number/{task_no}",
    response_model=TaskRead,
    dependencies=secured,
)
def read_task_by_number(
    task_no: str,
    include_deleted: bool = True,
    db: Session = Depends(get_db),
) -> TaskRead:
    normalized = task_no.strip().upper()
    query = (
        select(Task)
        .options(selectinload(Task.assignee))
        .where(Task.task_no == normalized)
    )
    if not include_deleted:
        query = query.where(Task.deleted_at.is_(None))
    task = db.scalar(query)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务 {normalized} 不存在")
    return task_read(task)


@app.post(
    "/api/v1/channel-task-completions",
    response_model=TaskCompletionResult,
    dependencies=[Depends(require_shared_api_key)],
)
def complete_channel_tasks(
    payload: ChannelCompletionRequest,
    db: Session = Depends(get_db),
) -> TaskCompletionResult:
    task_no = (payload.task_no or "").strip().upper()
    if payload.all_active == bool(task_no):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请明确指定一个任务编号或全部进行中任务",
        )

    staff = channel_staff(db, payload.platform, payload.recipient_id)
    if task_no:
        task = db.scalar(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(Task.task_no == task_no)
        )
        if not task:
            raise HTTPException(status_code=404, detail=f"任务 {task_no} 不存在")
        if task.assignee_id != staff.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只能完成当前消息账号负责的任务",
            )
        if task.deleted_at:
            raise HTTPException(status_code=409, detail="回收站任务不能直接完成")
        tasks = [task]
        targets = [] if task.status == "done" else tasks
    else:
        tasks = list(
            db.scalars(
                select(Task)
                .options(selectinload(Task.assignee))
                .where(
                    Task.assignee_id == staff.id,
                    Task.deleted_at.is_(None),
                    Task.status != "done",
                )
                .order_by(Task.id)
            ).all()
        )
        targets = tasks

    completed_at = utcnow()
    for task in targets:
        task.status = "done"
        task.completed_at = completed_at
        task.next_remind_at = None
        add_event(db, task, "completed", staff.name, "通过消息渠道完成任务")
    db.commit()
    return TaskCompletionResult(
        items=[task_read(task) for task in tasks],
        completed_count=len(targets),
    )


@app.get("/api/v1/tasks/{task_id}", response_model=TaskRead, dependencies=secured)
def read_task(task_id: int, db: Session = Depends(get_db)) -> TaskRead:
    return task_read(get_task(db, task_id))


@app.patch("/api/v1/tasks/{task_id}", response_model=TaskRead)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    principal: AuthPrincipal = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> TaskRead:
    task = get_task(db, task_id)
    values = payload.model_dump(exclude_unset=True)
    operator = values.pop("operator_name", "客户端用户")
    if values.get("status") == "done":
        operator = completion_operator(
            principal,
            values.get("assignee_id", task.assignee_id),
        )
    if "assignee_id" in values:
        if values["assignee_id"] != task.assignee_id:
            require_assignable(get_staff(db, values["assignee_id"]))
    for field, value in values.items():
        setattr(task, field, value)
    if "status" in values:
        if task.status == "done":
            task.completed_at = utcnow()
            task.next_remind_at = None
        elif task.completed_at:
            task.completed_at = None
            if task.remind_enabled:
                task.next_remind_at = utcnow() + timedelta(
                    minutes=task.remind_interval_minutes
                )
    add_event(db, task, "updated", operator, "修改任务")
    db.commit()
    return task_read(get_task(db, task.id))


@app.delete("/api/v1/tasks/{task_id}", response_model=TaskRead, dependencies=secured)
def delete_task(
    task_id: int,
    operator_name: str = "客户端用户",
    db: Session = Depends(get_db),
) -> TaskRead:
    task = get_task(db, task_id)
    task.deleted_at = utcnow()
    task.next_remind_at = None
    add_event(db, task, "deleted", operator_name, "删除任务")
    db.commit()
    return task_read(get_task(db, task.id, include_deleted=True))


@app.post("/api/v1/tasks/{task_id}/complete", response_model=TaskRead)
def complete_task(
    task_id: int,
    payload: ActionRequest,
    principal: AuthPrincipal = Depends(require_api_key),
    db: Session = Depends(get_db),
) -> TaskRead:
    task = get_task(db, task_id)
    operator = completion_operator(principal, task.assignee_id)
    task.status = "done"
    task.completed_at = utcnow()
    task.next_remind_at = None
    add_event(db, task, "completed", operator, "完成任务")
    db.commit()
    return task_read(get_task(db, task.id))


@app.post("/api/v1/tasks/{task_id}/reopen", response_model=TaskRead, dependencies=secured)
def reopen_task(
    task_id: int,
    payload: ActionRequest,
    db: Session = Depends(get_db),
) -> TaskRead:
    task = get_task(db, task_id)
    task.status = "todo"
    task.completed_at = None
    task.next_remind_at = (
        utcnow() + timedelta(minutes=task.remind_interval_minutes)
        if task.remind_enabled
        else None
    )
    add_event(db, task, "reopened", payload.operator_name, "重新打开任务")
    db.commit()
    return task_read(get_task(db, task.id))


@app.post("/api/v1/tasks/{task_id}/restore", response_model=TaskRead, dependencies=secured)
def restore_task(
    task_id: int,
    payload: ActionRequest,
    db: Session = Depends(get_db),
) -> TaskRead:
    task = get_task(db, task_id, include_deleted=True)
    task.deleted_at = None
    if task.status != "done" and task.remind_enabled:
        task.next_remind_at = utcnow() + timedelta(minutes=task.remind_interval_minutes)
    add_event(db, task, "restored", payload.operator_name, "恢复任务")
    db.commit()
    return task_read(get_task(db, task.id))


@app.get(
    "/api/v1/tasks/{task_id}/events",
    response_model=list[TaskEventRead],
    dependencies=secured,
)
def list_task_events(task_id: int, db: Session = Depends(get_db)) -> list[TaskEvent]:
    get_task(db, task_id, include_deleted=True)
    return list(
        db.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.desc())
        ).all()
    )
