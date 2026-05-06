"""
Admin command handlers for the Telegram Bot PPOB/SMM Reseller.

Handlers:
  - /confirm_topup <ref_code>          → handle_confirm_topup
  - /addsaldo <telegram_id> <amount>   → handle_addsaldo
  - /kurangsaldo <telegram_id> <amount>→ handle_kurangsaldo
  - /setharga <service_id> <margin>    → handle_setharga
  - /broadcast <message>               → handle_broadcast

All handlers:
  - Apply the @require_admin decorator (silently ignores non-admins)
  - Use get_session() context manager for DB access
  - Delegate business logic to service modules
  - Respond in Indonesian

Requirements: 3.4, 3.5, 5.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from telegram import Update
from telegram.ext import ContextTypes

from bot.database import get_session
from bot.middleware.admin_guard import require_admin
from bot.services import admin_service, service_catalog_service, topup_service
from bot.services.topup_service import TopUpError
from bot.services.wallet_service import InsufficientBalanceError
from bot.utils.formatters import format_rupiah

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /confirm_topup <ref_code>
# ---------------------------------------------------------------------------

@require_admin
async def handle_confirm_topup(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Confirm a pending manual top-up request.

    Usage: /confirm_topup <ref_code>
    Example: /confirm_topup TOPUP-ABC123

    Requirements: 3.4, 3.5
    """
    admin_id = update.effective_user.id

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "❌ Format salah. Gunakan: `/confirm_topup <ref_code>`\n"
            "Contoh: `/confirm_topup TOPUP-ABC123`",
            parse_mode="Markdown",
        )
        return

    ref_code = args[0].strip()

    try:
        async with get_session() as session:
            confirmed = await topup_service.confirm_topup(
                session=session,
                ref_code=ref_code,
                admin_id=admin_id,
            )

        await update.effective_message.reply_text(
            f"✅ *Top up berhasil dikonfirmasi!*\n"
            f"\n"
            f"🔑 Kode Referensi: `{confirmed.reference_code}`\n"
            f"👤 User ID: `{confirmed.user_id}`\n"
            f"💰 Nominal: *{format_rupiah(confirmed.amount)}*\n"
            f"📋 Status: Dikonfirmasi",
            parse_mode="Markdown",
        )

    except TopUpError as exc:
        await update.effective_message.reply_text(
            f"❌ *Gagal mengkonfirmasi top up*\n\n{exc}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error(
            "Error in handle_confirm_topup (admin=%d, ref=%s): %s",
            admin_id, ref_code, exc,
        )
        await update.effective_message.reply_text(
            "❌ Terjadi kesalahan. Silakan coba lagi nanti."
        )


# ---------------------------------------------------------------------------
# /addsaldo <telegram_id> <amount>
# ---------------------------------------------------------------------------

@require_admin
async def handle_addsaldo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Add balance to a user's wallet.

    Usage: /addsaldo <telegram_id> <amount>
    Example: /addsaldo 123456789 50000

    Requirements: 8.2
    """
    admin_id = update.effective_user.id

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "❌ Format salah. Gunakan: `/addsaldo <telegram_id> <nominal>`\n"
            "Contoh: `/addsaldo 123456789 50000`",
            parse_mode="Markdown",
        )
        return

    # Parse telegram_id
    try:
        target_telegram_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Telegram ID harus berupa angka."
        )
        return

    # Parse amount
    try:
        amount = Decimal(args[1])
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except (InvalidOperation, ValueError):
        await update.effective_message.reply_text(
            "❌ Nominal tidak valid. Masukkan angka positif.\n"
            "Contoh: `/addsaldo 123456789 50000`",
            parse_mode="Markdown",
        )
        return

    try:
        async with get_session() as session:
            await admin_service.add_balance(
                session=session,
                target_telegram_id=target_telegram_id,
                amount=amount,
                admin_id=admin_id,
            )
            # Fetch updated balance for confirmation message
            from bot.repositories import user_repository
            from bot.services.wallet_service import get_balance
            user = await user_repository.get_by_telegram_id(session, target_telegram_id)
            new_balance = await get_balance(session, user.id)

        await update.effective_message.reply_text(
            f"✅ *Saldo berhasil ditambahkan!*\n"
            f"\n"
            f"👤 Telegram ID: `{target_telegram_id}`\n"
            f"💰 Nominal ditambahkan: *{format_rupiah(amount)}*\n"
            f"💳 Saldo baru: *{format_rupiah(new_balance)}*",
            parse_mode="Markdown",
        )

    except ValueError as exc:
        await update.effective_message.reply_text(
            f"❌ *Gagal menambahkan saldo*\n\n{exc}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error(
            "Error in handle_addsaldo (admin=%d, target=%d): %s",
            admin_id, target_telegram_id, exc,
        )
        await update.effective_message.reply_text(
            "❌ Terjadi kesalahan. Silakan coba lagi nanti."
        )


# ---------------------------------------------------------------------------
# /kurangsaldo <telegram_id> <amount>
# ---------------------------------------------------------------------------

@require_admin
async def handle_kurangsaldo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Deduct balance from a user's wallet.

    Usage: /kurangsaldo <telegram_id> <amount>
    Example: /kurangsaldo 123456789 25000

    If the deduction would make the balance negative, shows the current
    balance to the admin instead of proceeding.

    Requirements: 8.3, 8.4
    """
    admin_id = update.effective_user.id

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "❌ Format salah. Gunakan: `/kurangsaldo <telegram_id> <nominal>`\n"
            "Contoh: `/kurangsaldo 123456789 25000`",
            parse_mode="Markdown",
        )
        return

    # Parse telegram_id
    try:
        target_telegram_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Telegram ID harus berupa angka."
        )
        return

    # Parse amount
    try:
        amount = Decimal(args[1])
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except (InvalidOperation, ValueError):
        await update.effective_message.reply_text(
            "❌ Nominal tidak valid. Masukkan angka positif.\n"
            "Contoh: `/kurangsaldo 123456789 25000`",
            parse_mode="Markdown",
        )
        return

    try:
        async with get_session() as session:
            await admin_service.deduct_balance(
                session=session,
                target_telegram_id=target_telegram_id,
                amount=amount,
                admin_id=admin_id,
            )
            # Fetch updated balance for confirmation message
            from bot.repositories import user_repository
            from bot.services.wallet_service import get_balance
            user = await user_repository.get_by_telegram_id(session, target_telegram_id)
            new_balance = await get_balance(session, user.id)

        await update.effective_message.reply_text(
            f"✅ *Saldo berhasil dikurangi!*\n"
            f"\n"
            f"👤 Telegram ID: `{target_telegram_id}`\n"
            f"💰 Nominal dikurangi: *{format_rupiah(amount)}*\n"
            f"💳 Saldo baru: *{format_rupiah(new_balance)}*",
            parse_mode="Markdown",
        )

    except InsufficientBalanceError as exc:
        await update.effective_message.reply_text(
            f"❌ *Saldo tidak mencukupi untuk dikurangi*\n"
            f"\n"
            f"👤 Telegram ID: `{target_telegram_id}`\n"
            f"💳 Saldo saat ini: *{format_rupiah(exc.current_balance)}*\n"
            f"💰 Nominal yang diminta: *{format_rupiah(exc.requested_amount)}*\n"
            f"\n"
            f"Pengurangan dibatalkan.",
            parse_mode="Markdown",
        )
    except ValueError as exc:
        await update.effective_message.reply_text(
            f"❌ *Gagal mengurangi saldo*\n\n{exc}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error(
            "Error in handle_kurangsaldo (admin=%d, target=%d): %s",
            admin_id, target_telegram_id, exc,
        )
        await update.effective_message.reply_text(
            "❌ Terjadi kesalahan. Silakan coba lagi nanti."
        )


# ---------------------------------------------------------------------------
# /setharga <service_id> <margin>
# ---------------------------------------------------------------------------

@require_admin
async def handle_setharga(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Update the margin (and thus sell price) for a service.

    Usage: /setharga <service_id> <margin>
    Example: /setharga 42 5000

    Requirements: 5.5
    """
    admin_id = update.effective_user.id

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "❌ Format salah. Gunakan: `/setharga <service_id> <margin>`\n"
            "Contoh: `/setharga 42 5000`",
            parse_mode="Markdown",
        )
        return

    # Parse service_id
    try:
        service_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Service ID harus berupa angka."
        )
        return

    # Parse margin
    try:
        margin = Decimal(args[1])
        if margin < 0:
            raise ValueError("Margin must be non-negative")
    except (InvalidOperation, ValueError):
        await update.effective_message.reply_text(
            "❌ Margin tidak valid. Masukkan angka non-negatif.\n"
            "Contoh: `/setharga 42 5000`",
            parse_mode="Markdown",
        )
        return

    try:
        async with get_session() as session:
            # Capture old sell price before update
            from bot.repositories import service_repository
            old_service = await service_repository.get_by_id(session, service_id)
            old_sell_price = old_service.sell_price if old_service else None

            updated_service = await service_catalog_service.set_margin(
                session=session,
                service_id=service_id,
                margin=margin,
                admin_id=admin_id,
            )

        old_price_str = (
            format_rupiah(old_sell_price) if old_sell_price is not None else "N/A"
        )

        await update.effective_message.reply_text(
            f"✅ *Harga layanan berhasil diperbarui!*\n"
            f"\n"
            f"🔹 Layanan: *{updated_service.name}*\n"
            f"🆔 Service ID: `{updated_service.id}`\n"
            f"💰 Margin baru: *{format_rupiah(margin)}*\n"
            f"📉 Harga jual lama: {old_price_str}\n"
            f"📈 Harga jual baru: *{format_rupiah(updated_service.sell_price)}*",
            parse_mode="Markdown",
        )

    except ValueError as exc:
        await update.effective_message.reply_text(
            f"❌ *Gagal memperbarui harga*\n\n{exc}",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error(
            "Error in handle_setharga (admin=%d, service=%d): %s",
            admin_id, service_id, exc,
        )
        await update.effective_message.reply_text(
            "❌ Terjadi kesalahan. Silakan coba lagi nanti."
        )


# ---------------------------------------------------------------------------
# /broadcast <message>
# ---------------------------------------------------------------------------

@require_admin
async def handle_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Broadcast a message to all active users.

    Usage: /broadcast <pesan>
    Example: /broadcast Halo semua! Ada promo hari ini.

    Requirements: 8.5
    """
    admin_id = update.effective_user.id

    # Extract message text — everything after "/broadcast "
    full_text = update.effective_message.text or ""
    # Strip the command prefix (handles both "/broadcast" and "/broadcast@botname")
    if " " not in full_text:
        await update.effective_message.reply_text(
            "❌ Format salah. Gunakan: `/broadcast <pesan>`\n"
            "Contoh: `/broadcast Halo semua! Ada promo hari ini.`",
            parse_mode="Markdown",
        )
        return

    # Everything after the first space is the broadcast message
    broadcast_message = full_text.split(" ", 1)[1].strip()
    if not broadcast_message:
        await update.effective_message.reply_text(
            "❌ Pesan broadcast tidak boleh kosong."
        )
        return

    # Get bot_app from context.bot_data
    bot_app = context.bot_data.get("bot_app")
    if bot_app is None:
        logger.error("bot_app not found in bot_data for broadcast by admin %d", admin_id)
        await update.effective_message.reply_text(
            "❌ Layanan broadcast sedang tidak tersedia. Silakan coba lagi nanti."
        )
        return

    # Notify admin that broadcast is starting
    await update.effective_message.reply_text(
        "📢 Memulai broadcast... Harap tunggu."
    )

    try:
        async with get_session() as session:
            result = await admin_service.broadcast(
                session=session,
                message=broadcast_message,
                admin_id=admin_id,
                bot_app=bot_app,
            )

        await update.effective_message.reply_text(
            f"✅ *Broadcast selesai!*\n"
            f"\n"
            f"👥 Total pengguna: {result.total}\n"
            f"✅ Berhasil dikirim: {result.success}\n"
            f"❌ Gagal: {result.failed}",
            parse_mode="Markdown",
        )

    except Exception as exc:
        logger.error(
            "Error in handle_broadcast (admin=%d): %s", admin_id, exc
        )
        await update.effective_message.reply_text(
            "❌ Terjadi kesalahan saat broadcast. Silakan coba lagi nanti."
        )
