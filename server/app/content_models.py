from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class ContentAccount(Base):
    __tablename__ = "content_account"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("user_account.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    document: Mapped[dict] = mapped_column(JSON)
    __mapper_args__ = {"version_id_col": revision}


class ContentEpisode(Base):
    __tablename__ = "content_episode"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("content_account.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    document: Mapped[dict] = mapped_column(JSON)
    __mapper_args__ = {"version_id_col": revision}
