"""
User-facing command handlers.

UX menggunakan ReplyKeyboardMarkup (keyboard permanen di bawah chat)
untuk navigasi utama, dan InlineKeyboardMarkup untuk aksi kontekstual.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import ContextTypes

from bot.database import get_session
from bot.middleware.rate_limiter import rate_limit
from bot.services import (
    order_service,
    service_catalog_service,
    topup_service,
    user_service,
)
from bot.services.order_service import (
    InsufficientBalanceOrderError,
    InvalidTargetError,
    ServiceNotFoundError,
)
from bot.services.topup_service import TopUpError
from bot.utils.formatters import (
    format_history,
    format_order_status,
    format_profile,
    format_rupiah,
    format_topup_instruction,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persistent bottom keyboard (ReplyKeyboard)
# ---------------------------------------------------------------------------

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🛒 Layanan", "💳 Top Up"],
        ["👤 Profil", "💰 Saldo"],
        ["📜 Riwayat"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


# ---------------------------------------------------------------------------
# Inline keyboard helpers
# ---------------------------------------------------------------------------

def kb_topup_amounts() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Rp 10.000", callback_data="topup_10000"),
            InlineKeyboardButton("Rp 25.000", callback_data="topup_25000"),
            InlineKeyboardButton("Rp 50.000", callback_data="topup_50000"),
        ],
        [
            InlineKeyboardButton("Rp 100.000", callback_data="topup_100000"),
            InlineKeyboardButton("Rp 250.000", callback_data="topup_250000"),
            InlineKeyboardButton("Rp 500.000", callback_data="topup_500000"),
        ],
    ])


def kb_order_done(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔍 Cek Status Order #{order_id}", callback_data=f"cekorder_{order_id}")],
    ])


def kb_order_status(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Status", callback_data=f"cekorder_{order_id}")],
    ])


def kb_topup_action() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Top Up Sekarang", callback_data="cmd_topup_info")],
    ])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _ensure_user(session, telegram_id: int, username):
    """Update username and return profile. Returns None if not registered."""
    try:
        await user_service.update_username(session, telegram_id, username)
    except Exception:
        pass
    return await user_service.get_profile(session, telegram_id)


async def _send_or_edit(update: Update, text: str, keyboard=None, parse_mode="Markdown"):
    """Send new message or edit existing (for callback queries)."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=parse_mode, reply_markup=keyboard
            )
            return
        except Exception:
            pass
        await update.callback_query.message.reply_text(
            text, parse_mode=parse_mode, reply_markup=keyboard
        )
    else:
        await update.effective_message.reply_text(
            text, parse_mode=parse_mode, reply_markup=keyboard
        )


# ---------------------------------------------------------------------------
# Text message router (handles ReplyKeyboard button taps)
# ---------------------------------------------------------------------------

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route persistent keyboard button taps to the correct handler."""
    text = (update.effective_message.text or "").strip()
    routes = {
        "🛒 Layanan": handle_services,
        "💳 Top Up": handle_topup,
        "👤 Profil": handle_profile,
        "💰 Saldo": handle_saldo,
        "📜 Riwayat": handle_history,
    }
    handler = routes.get(text)
    if handler:
        await handler(update, context)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@rate_limit
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return
    try:
        async with get_session() as session:
            user, created = await user_service.get_or_create_user(
                session, tg_user.id, tg_user.username
            )
            if not created:
                await user_service.update_username(session, tg_user.id, tg_user.username)
                profile = await user_service.get_profile(session, tg_user.id)
            else:
                profile = None

        if created:
            text = (
                "👋 *Selamat datang di Bot PPOB/SMM Reseller!*\n\n"
                f"✅ Akun berhasil dibuat\n"
                f"🆔 User ID: `{user.id}`\n\n"
                "Gunakan tombol di bawah untuk navigasi:"
            )
        else:
            bal = format_rupiah(profile.balance) if profile else "Rp 0"
            text = (
                f"👋 *Selamat datang kembali!*\n\n"
                f"💰 Saldo: *{bal}*\n\n"
                "Gunakan tombol di bawah:"
            )

        await update.effective_message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )

    except Exception as exc:
        logger.error("handle_start error user %d: %s", tg_user.id, exc, exc_info=True)
        await update.effective_message.reply_text("❌ Terjadi kesalahan. Silakan coba lagi.")


# ---------------------------------------------------------------------------
# /profile
# ---------------------------------------------------------------------------

@rate_limit
async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return
    try:
        async with get_session() as session:
            profile = await _ensure_user(session, tg_user.id, tg_user.username)

        if profile is None:
            await update.effective_message.reply_text(
                "❌ Akun tidak ditemukan. Ketik /start untuk mendaftar.",
                reply_markup=MAIN_KEYBOARD,
            )
            return

        await update.effective_message.reply_text(
            format_profile(profile),
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )

    except Exception as exc:
        logger.error("handle_profile error user %d: %s", tg_user.id, exc, exc_info=True)
        await update.effective_message.reply_text("❌ Terjadi kesalahan.")


# ---------------------------------------------------------------------------
# /saldo
# ---------------------------------------------------------------------------

@rate_limit
async def handle_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return
    try:
        async with get_session() as session:
            profile = await _ensure_user(session, tg_user.id, tg_user.username)

        if profile is None:
            await update.effective_message.reply_text(
                "❌ Akun tidak ditemukan. Ketik /start untuk mendaftar."
            )
            return

        await update.effective_message.reply_text(
            f"💰 *Saldo Anda saat ini:*\n\n*{format_rupiah(profile.balance)}*",
            parse_mode="Markdown",
            reply_markup=kb_topup_action(),
        )

    except Exception as exc:
        logger.error("handle_saldo error user %d: %s", tg_user.id, exc, exc_info=True)
        await update.effective_message.reply_text("❌ Terjadi kesalahan.")


# ---------------------------------------------------------------------------
# /topup
# ---------------------------------------------------------------------------

@rate_limit
async def handle_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "💳 *Top Up Saldo*\n\nPilih nominal atau ketik `/topup <nominal>`:",
            parse_mode="Markdown",
            reply_markup=kb_topup_amounts(),
        )
        return

    try:
        amount = Decimal(args[0])
    except (InvalidOperation, ValueError):
        await update.effective_message.reply_text(
            "❌ Nominal tidak valid. Contoh: `/topup 50000`",
            parse_mode="Markdown",
        )
        return

    await _do_topup(update, context, tg_user.id, tg_user.username, amount)


async def _do_topup(update, context, telegram_id: int, username, amount: Decimal) -> None:
    """Core topup logic — shared by command and callback."""
    try:
        async with get_session() as session:
            profile = await _ensure_user(session, telegram_id, username)
            if profile is None:
                await _send_or_edit(update, "❌ Akun tidak ditemukan. Ketik /start untuk mendaftar.")
                return
            topup = await topup_service.create_manual_topup(session, profile.id, amount)

        await _send_or_edit(update, format_topup_instruction(topup))

    except TopUpError as exc:
        await _send_or_edit(update, f"❌ {exc}", kb_topup_amounts())
    except Exception as exc:
        logger.error("topup error user %d: %s", telegram_id, exc, exc_info=True)
        await _send_or_edit(update, "❌ Terjadi kesalahan.")


# ---------------------------------------------------------------------------
# /services — kategori sebagai tombol, auto-fetch jika kosong
# ---------------------------------------------------------------------------

@rate_limit
async def handle_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return

    ppob_client = context.bot_data.get("ppob_client")
    await _show_categories(update, context, tg_user.id, tg_user.username, ppob_client)


async def _show_categories(update, context, telegram_id: int, username, ppob_client, force_refresh=False) -> None:
    """Show service categories as inline buttons."""
    try:
        async with get_session() as session:
            try:
                await user_service.update_username(session, telegram_id, username)
            except Exception:
                pass

            services, is_fresh = await service_catalog_service.get_services(session)

            # Auto-refresh if empty or forced
            if (not services or force_refresh) and ppob_client:
                try:
                    await service_catalog_service.refresh_cache(session, ppob_client)
                    services, is_fresh = await service_catalog_service.get_services(session)
                except Exception as e:
                    logger.warning("Service cache refresh failed: %s", e)

        if not services:
            await update.effective_message.reply_text(
                "ℹ️ Belum ada layanan. Coba lagi nanti.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="cmd_services_refresh")]
                ]),
            )
            return

        # Group by category
        categories: dict[str, list] = {}
        for svc in services:
            cat = svc.category or "Lainnya"
            categories.setdefault(cat, []).append(svc)

        # Build category buttons (3 per row)
        cat_names = sorted(categories.keys())
        rows = []
        row = []
        for cat in cat_names:
            row.append(InlineKeyboardButton(cat, callback_data=f"cat_{cat[:30]}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        # Bottom action buttons
        rows.append([
            InlineKeyboardButton("📋 Semua Produk", callback_data="cat_ALL"),
            InlineKeyboardButton("🔄 Refresh", callback_data="cmd_services_refresh"),
        ])

        stale_note = "\n⚠️ _Data mungkin tidak terkini_" if not is_fresh else ""
        text = f"🛒 *Pilih Kategori Layanan*{stale_note}\n\nTotal: {len(services)} layanan tersedia"

        keyboard = InlineKeyboardMarkup(rows)

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(
                    text, parse_mode="Markdown", reply_markup=keyboard
                )
                return
            except Exception:
                pass
            await update.callback_query.message.reply_text(
                text, parse_mode="Markdown", reply_markup=keyboard
            )
        else:
            await update.effective_message.reply_text(
                text, parse_mode="Markdown", reply_markup=keyboard
            )

        # Store services in context for category browsing
        context.user_data["services_cache"] = {svc.id: svc for svc in services}
        context.user_data["services_by_cat"] = categories

    except Exception as exc:
        logger.error("handle_services error user %d: %s", telegram_id, exc, exc_info=True)
        await update.effective_message.reply_text("❌ Terjadi kesalahan.")


async def _show_category_services(update, context, category: str) -> None:
    """Show services in a specific category as numbered inline buttons."""
    categories = context.user_data.get("services_by_cat", {})
    all_services_cache = context.user_data.get("services_cache", {})

    if category == "ALL":
        services = list(all_services_cache.values())
        title = "📋 *Semua Produk*"
    else:
        services = categories.get(category, [])
        title = f"🛒 *{category}*"

    if not services:
        await update.callback_query.answer("Tidak ada layanan di kategori ini.")
        return

    # Build numbered buttons (7 per row like reference image)
    rows = []
    row = []
    for svc in services:
        row.append(InlineKeyboardButton(str(svc.id), callback_data=f"svc_{svc.id}"))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Bottom navigation
    rows.append([
        InlineKeyboardButton("◀️ Kategori", callback_data="cmd_services"),
    ])

    text = f"{title}\n\nTap nomor untuk melihat detail & order:"
    try:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )
    except Exception:
        await update.callback_query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )


async def _show_service_detail(update, context, service_id: int) -> None:
    """Show detail of a single service with order button."""
    all_services = context.user_data.get("services_cache", {})
    svc = all_services.get(service_id)

    if svc is None:
        await update.callback_query.answer("Layanan tidak ditemukan.")
        return

    desc = svc.description or "Tidak ada deskripsi"
    text = (
        f"📦 *{svc.name}*\n\n"
        f"🆔 ID: `{svc.id}`\n"
        f"💰 Harga: *{format_rupiah(svc.sell_price)}*\n"
        f"📁 Kategori: {svc.category or '-'}\n\n"
        f"📝 {desc}\n\n"
        f"Untuk order ketik:\n`/order {svc.id} <target>`"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Kembali", callback_data=f"cat_{svc.category or 'Lainnya'}")],
    ])

    try:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )
    except Exception:
        await update.callback_query.message.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard
        )


# ---------------------------------------------------------------------------
# /order <service_id> <target>
# ---------------------------------------------------------------------------

@rate_limit
async def handle_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "❌ Format: `/order <service_id> <target>`\n"
            "Contoh: `/order 42 https://instagram.com/myprofile`\n\n"
            "Ketik 🛒 *Layanan* untuk melihat daftar.",
            parse_mode="Markdown",
        )
        return

    try:
        service_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Service ID harus berupa angka.")
        return

    target = args[1]
    ppob_client = context.bot_data.get("ppob_client")
    bot_app = context.bot_data.get("bot_app")

    if ppob_client is None:
        await update.effective_message.reply_text("❌ Layanan sedang tidak tersedia.")
        return

    try:
        async with get_session() as session:
            profile = await _ensure_user(session, tg_user.id, tg_user.username)
            if profile is None:
                await update.effective_message.reply_text(
                    "❌ Akun tidak ditemukan. Ketik /start untuk mendaftar."
                )
                return

            order = await order_service.create_order(
                session=session,
                user_id=profile.id,
                service_id=service_id,
                target=target,
                ppob_client=ppob_client,
                bot_app=bot_app,
            )

        await update.effective_message.reply_text(
            f"✅ *Order berhasil dibuat!*\n\n"
            f"🆔 Order ID: `{order.id}`\n"
            f"🎯 Target: `{order.target}`\n"
            f"💰 {format_rupiah(order.amount)}\n"
            f"🔄 Status: Sedang diproses",
            parse_mode="Markdown",
            reply_markup=kb_order_done(order.id),
        )

    except ServiceNotFoundError:
        await update.effective_message.reply_text(
            f"❌ Layanan ID `{service_id}` tidak ditemukan.",
            parse_mode="Markdown",
        )
    except InsufficientBalanceOrderError as exc:
        await update.effective_message.reply_text(
            f"❌ *Saldo tidak mencukupi*\n\n"
            f"💰 Saldo: {format_rupiah(exc.current_balance)}\n"
            f"💳 Dibutuhkan: {format_rupiah(exc.required_amount)}\n"
            f"📉 Kurang: {format_rupiah(exc.required_amount - exc.current_balance)}",
            parse_mode="Markdown",
            reply_markup=kb_topup_action(),
        )
    except InvalidTargetError:
        await update.effective_message.reply_text(
            f"❌ Target `{target}` tidak valid untuk layanan ini.",
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.error("handle_order error user %d: %s", tg_user.id, exc, exc_info=True)
        await update.effective_message.reply_text("❌ Terjadi kesalahan.")


# ---------------------------------------------------------------------------
# /cekorder <order_id>
# ---------------------------------------------------------------------------

@rate_limit
async def handle_cekorder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "❌ Format: `/cekorder <order_id>`\nContoh: `/cekorder 123`",
            parse_mode="Markdown",
        )
        return

    try:
        order_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Order ID harus berupa angka.")
        return

    await _show_order(update, context, tg_user.id, tg_user.username, order_id)


async def _show_order(update, context, telegram_id: int, username, order_id: int) -> None:
    try:
        async with get_session() as session:
            profile = await _ensure_user(session, telegram_id, username)
            if profile is None:
                await _send_or_edit(update, "❌ Akun tidak ditemukan.")
                return
            order = await order_service.get_order(session, order_id, profile.id)

        if order is None:
            await _send_or_edit(update, f"❌ Order `#{order_id}` tidak ditemukan atau bukan milik Anda.")
            return

        await _send_or_edit(update, format_order_status(order), kb_order_status(order_id))

    except Exception as exc:
        logger.error("handle_cekorder error user %d: %s", telegram_id, exc, exc_info=True)
        await _send_or_edit(update, "❌ Terjadi kesalahan.")


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

@rate_limit
async def handle_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user
    if tg_user is None:
        return
    try:
        async with get_session() as session:
            profile = await _ensure_user(session, tg_user.id, tg_user.username)
            if profile is None:
                await update.effective_message.reply_text("❌ Akun tidak ditemukan.")
                return
            orders = await order_service.get_history(session, profile.id, limit=10)

        await update.effective_message.reply_text(
            format_history(orders),
            parse_mode="Markdown",
        )

    except Exception as exc:
        logger.error("handle_history error user %d: %s", tg_user.id, exc, exc_info=True)
        await update.effective_message.reply_text("❌ Terjadi kesalahan.")
