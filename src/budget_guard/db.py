from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Numeric, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings

class Base(DeclarativeBase):
    pass

class BillDetail(Base):
    __tablename__ = "bill_details"
    id: Mapped[int] = mapped_column(primary_key=True)
    unique_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    billing_cycle: Mapped[str] = mapped_column(String(7), index=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 8))
    resource_id: Mapped[str] = mapped_column(String(256), default="")
    product: Mapped[str] = mapped_column(String(128), default="")
    raw_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

class EnforcementEvent(Base):
    __tablename__ = "enforcement_events"
    __table_args__ = (UniqueConstraint("project_id", "billing_cycle", "state", name="uq_event_state"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    billing_cycle: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Numeric(20, 8))
    budget: Mapped[float] = mapped_column(Numeric(20, 8))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class AppliedState(Base):
    __tablename__ = "applied_states"
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    budget_type: Mapped[str] = mapped_column(String(16))
    window_key: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

class ControlledResource(Base):
    __tablename__ = "controlled_resources"
    __table_args__ = (
        UniqueConstraint("project_id", "resource_type", "resource_id", name="uq_controlled_resource"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(256))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

def init_db() -> None:
    Base.metadata.create_all(engine)
