"""
Message formatting utilities for the Telegram Bot PPOB/SMM Reseller.

All user-facing messages are in Indonesian.

Functions:
  - format_rupiah(amount)            — "Rp 50.000"
  - format_profile(profile)          — profil pengguna
  - format_order_status(order)       — status order
  - format_service_list(services)    — daftar layanan
  - format_history(orders)           — riwayat transaksi
  - format_topup_instruction(topup)  — instruksi top up manual

Requirements: 2.1, 2.3, 5.1, 6.6, 7.1, 7.3
"""
from __future__ import annotations

from decimal import Decimal
from typing import Union

from bot.models.db_models import Order, Service, TopUpRequest
from bot.services.user_service import UserProfile


# ---------------------------------------------------------------------------
# Monetary formatting
# ---------------------------------------------------------------------------

def format_rupiah(amount: Union[Decimal, int, float]) -> str:
    """Format *amount* as Indonesian Rupiah with dot thousands separator.

    Examples:
        >>> format_rupiah(50000)
        'Rp 50.000'
        >>> format_rupiah(Decimal('1500000'))
        'Rp 1.500.000'
        >>> format_rupiah(0)
        'Rp 0'

    Requirements: 2.3
    """
    # Convert to int (truncate decimals for display — balances are stored as
    # NUMERIC(15,2) but Rupiah is always displayed as whole numbers)
    value = int(Decimal(str(amount)))
    # Format with dot as thousands separator (Indonesian convention)
    formatted = f"{value:,}".replace(",", ".")
    return f"Rp {formatted}"


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def format_profile(profile: UserProfile) -> str:
    """Format a user profile message.

    Requirements: 2.1
    """
    username_display = f"@{profile.username}" if profile.username else "(tidak ada)"
    status_display = "✅ Aktif" if profile.is_active else "❌ Nonaktif"

    return (
        "👤 *Profil Akun*\n"
        "\n"
        f"🆔 User ID: `{profile.id}`\n"
        f"📱 Telegram ID: `{profile.telegram_id}`\n"
        f"👤 Username: {username_display}\n"
        f"💰 Saldo: *{format_rupiah(profile.balance)}*\n"
        f"📦 Total Order: {profile.total_orders}\n"
        f"🔘 Status: {status_display}"
    )


# ---------------------------------------------------------------------------
# Order status
# ---------------------------------------------------------------------------

_STATUS_EMOJI = {
    "pending": "⏳",
    "processing": "🔄",
    "success": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}

_STATUS_LABEL = {
    "pending": "Menunggu",
    "processing": "Diproses",
    "success": "Berhasil",
    "failed": "Gagal",
    "cancelled": "Dibatalkan",
}


def format_order_status(order: Order) -> str:
    """Format an order status message.

    Requirements: 7.1
    """
    emoji = _STATUS_EMOJI.get(order.status, "❓")
    label = _STATUS_LABEL.get(order.status, order.status.capitalize())

    # Service name — use relationship if loaded, otherwise show ID
    service_name: str
    if hasattr(order, "service") and order.service is not None:
        service_name = order.service.name
    else:
        service_name = f"Layanan #{order.service_id}"

    created_str = (
        order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else "-"
    )
    updated_str = (
        order.updated_at.strftime("%d/%m/%Y %H:%M") if order.updated_at else "-"
    )

    lines = [
        f"📋 *Detail Order #{order.id}*",
        "",
        f"📦 Layanan: {service_name}",
        f"🎯 Target: `{order.target}`",
        f"💰 Jumlah: {format_rupiah(order.amount)}",
        f"{emoji} Status: *{label}*",
        f"🕐 Dibuat: {created_str}",
        f"🔄 Diperbarui: {updated_str}",
    ]

    if order.status_message:
        msg = order.status_message
        # Sembunyikan pesan teknis/error raw dari user biasa
        if "PPOB" in msg or "HTTP" in msg or "{" in msg:
            msg = "Layanan sedang gangguan dari pusat / maintenance. Saldo Anda telah otomatis dikembalikan."
        lines.append(f"📝 Keterangan: {msg}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Service list
# ---------------------------------------------------------------------------

def format_service_list(services: list, is_fresh: bool = True) -> str:
    """Format the list of available services.

    Requirements: 5.1
    """
    if not services:
        return "ℹ️ Tidak ada layanan yang tersedia saat ini."

    header = "🛒 *Daftar Layanan Tersedia*\n"
    if not is_fresh:
        header += "⚠️ _Data mungkin tidak terkini (cache terakhir)_\n"
    header += "\n"

    lines = [header]
    for svc in services:
        description = svc.description or "Tidak ada deskripsi"
        # Truncate long descriptions
        if len(description) > 80:
            description = description[:77] + "..."

        lines.append(
            f"🔹 *[{svc.id}] {svc.name}*\n"
            f"   📝 {description}\n"
            f"   💰 Harga: {format_rupiah(svc.sell_price)}\n"
        )

    lines.append(
        "💡 Gunakan `/order <ID> <target>` untuk memesan."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Transaction history
# ---------------------------------------------------------------------------

def format_history(orders: list) -> str:
    """Format the transaction history list (last 10 orders).

    Requirements: 7.3
    """
    if not orders:
        return "📭 Belum ada riwayat transaksi."

    lines = ["📜 *Riwayat Transaksi (10 Terakhir)*\n"]

    for order in orders:
        emoji = _STATUS_EMOJI.get(order.status, "❓")
        label = _STATUS_LABEL.get(order.status, order.status.capitalize())

        # Service name
        if hasattr(order, "service") and order.service is not None:
            service_name = order.service.name
        else:
            service_name = f"Layanan #{order.service_id}"

        created_str = (
            order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else "-"
        )

        lines.append(
            f"{emoji} *Order #{order.id}* — {label}\n"
            f"   📦 {service_name}\n"
            f"   🎯 `{order.target}`\n"
            f"   💰 {format_rupiah(order.amount)} | 🕐 {created_str}\n"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-up instruction
# ---------------------------------------------------------------------------

def format_topup_instruction(topup: TopUpRequest) -> str:
    """Format the manual top-up instruction message.

    Requirements: 3.1
    """
    expires_str = (
        topup.expires_at.strftime("%d/%m/%Y %H:%M UTC")
        if topup.expires_at
        else "-"
    )

    return (
        "💳 *Instruksi Top Up Manual*\n"
        "\n"
        f"💰 Nominal: *{format_rupiah(topup.amount)}*\n"
        f"🔑 Kode Referensi: `{topup.reference_code}`\n"
        f"⏰ Batas Waktu: {expires_str}\n"
        "\n"
        "📋 *Cara Pembayaran:*\n"
        "1. Transfer sejumlah nominal di atas ke rekening admin\n"
        "2. Sertakan kode referensi di keterangan transfer\n"
        "3. Hubungi admin untuk konfirmasi pembayaran\n"
        "\n"
        "⚠️ _Top up akan otomatis dibatalkan jika tidak dikonfirmasi "
        "sebelum batas waktu._"
    )
