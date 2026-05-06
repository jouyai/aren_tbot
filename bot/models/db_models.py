"""
SQLAlchemy ORM models for the Telegram Bot PPOB/SMM Reseller.

Tables:
  - users
  - topup_requests
  - services
  - orders
  - audit_logs
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import DateTime

# SQLAlchemy 2.0.40+ removed TIMESTAMPTZ from the postgresql dialect.
# Use DateTime(timezone=True) which maps to TIMESTAMPTZ on PostgreSQL.
TIMESTAMPTZ = DateTime(timezone=True)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """Registered Telegram users."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, default=Decimal("0.00")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    topup_requests: Mapped[list["TopUpRequest"]] = relationship(
        "TopUpRequest", back_populates="user", lazy="select"
    )
    orders: Mapped[list["Order"]] = relationship(
        "Order", back_populates="user", lazy="select"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog", back_populates="user", lazy="select"
    )

    __table_args__ = (Index("idx_users_telegram_id", "telegram_id"),)

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_id={self.telegram_id}>"


class TopUpRequest(Base):
    """Top-up requests (manual and automatic via payment gateway)."""

    __tablename__ = "topup_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    reference_code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # method: 'manual' | 'auto'
    method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual"
    )
    # status: 'pending' | 'confirmed' | 'expired' | 'failed'
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    payment_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMPTZ, nullable=True
    )
    confirmed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="topup_requests")

    __table_args__ = (
        Index("idx_topup_user_id", "user_id"),
        Index("idx_topup_reference", "reference_code"),
        Index("idx_topup_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<TopUpRequest id={self.id} ref={self.reference_code} status={self.status}>"


class Service(Base):
    """Digital services available from the PPOB provider."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    base_price: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    margin: Mapped[Decimal] = mapped_column(
        Numeric(15, 2), nullable=False, default=Decimal("0.00")
    )
    # sell_price is a generated/computed column: base_price + margin
    sell_price: Mapped[Decimal] = mapped_column(
        Numeric(15, 2),
        Computed("base_price + margin", persisted=True),
        nullable=False,
    )
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cached_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    orders: Mapped[list["Order"]] = relationship(
        "Order", back_populates="service", lazy="select"
    )

    __table_args__ = (
        Index("idx_services_provider_id", "provider_id"),
        Index("idx_services_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Service id={self.id} provider_id={self.provider_id} name={self.name!r}>"


class Order(Base):
    """Orders placed by users for digital services."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("services.id"), nullable=False
    )
    provider_order_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    target: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    # status: 'pending' | 'processing' | 'success' | 'failed' | 'cancelled'
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    status_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMPTZ, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="orders")
    service: Mapped["Service"] = relationship("Service", back_populates="orders")

    __table_args__ = (
        Index("idx_orders_user_id", "user_id"),
        Index("idx_orders_status", "status"),
        Index("idx_orders_provider_order_id", "provider_order_id"),
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status}>"


class AuditLog(Base):
    """Append-only financial audit log for all wallet mutations."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    # action examples: 'topup_request', 'topup_confirm', 'topup_expire',
    # 'order_create', 'order_success', 'order_failed', 'admin_add_balance',
    # 'admin_deduct_balance', 'webhook_received', 'webhook_invalid',
    # 'ppob_request', 'ppob_response'
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 2), nullable=True)
    balance_before: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    balance_after: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    reference_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    extra_data: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(
        "User", back_populates="audit_logs"
    )

    __table_args__ = (
        Index("idx_audit_user_id", "user_id"),
        Index("idx_audit_action", "action"),
        Index("idx_audit_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action}>"
