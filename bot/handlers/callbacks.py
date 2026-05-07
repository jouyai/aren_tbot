"""
Callback query handler — handles all InlineKeyboard button presses.

Callback data:
  cmd_start              → main menu
  cmd_profile            → profile
  cmd_saldo              → balance
  cmd_topup_info         → topup amount picker
  topup_<amount>         → process topup (e.g. topup_50000)
  cmd_services           → service categories
  cmd_services_refresh   → force-refresh from API
  cat_<name>             → show services in category (cat_ALL = all)
  svc_<id>               → show service detail
  svcnum_<n>             → show service detail by position number in current list
  cekorder_<id>          → show order status
"""
from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all callback queries."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    tg_user = update.effective_user

    from bot.handlers.user_commands import (
        _do_topup,
        _show_categories,
        _show_category_services,
        _show_order,
        _show_service_detail,
        handle_history,
        handle_profile,
        handle_saldo,
        handle_start,
        kb_topup_amounts,
        _send_or_edit,
    )

    ppob_client = context.bot_data.get("ppob_client")

    if data == "cmd_start":
        await handle_start(update, context)

    elif data == "cmd_profile":
        await handle_profile(update, context)

    elif data == "cmd_saldo":
        await handle_saldo(update, context)

    elif data == "cmd_topup_info":
        await _send_or_edit(
            update,
            "💳 *Top Up Saldo*\n\nPilih nominal atau ketik `/topup <nominal>`:",
            kb_topup_amounts(),
        )

    elif data.startswith("topup_"):
        try:
            amount = Decimal(data.split("_", 1)[1])
            await _do_topup(update, context, tg_user.id, tg_user.username, amount)
        except Exception as exc:
            logger.error("Callback topup error: %s", exc, exc_info=True)
            await query.message.reply_text("❌ Terjadi kesalahan.")

    elif data == "cmd_services":
        await _show_categories(update, context, tg_user.id, tg_user.username, ppob_client, force_refresh=False)

    elif data == "cmd_services_refresh":
        await query.answer("🔄 Memuat ulang layanan...")
        await _show_categories(update, context, tg_user.id, tg_user.username, ppob_client, force_refresh=True)

    elif data.startswith("cat_"):
        category = data[4:]  # strip "cat_"
        await _show_category_services(update, context, category)

    elif data.startswith("svc_"):
        try:
            service_id = int(data[4:])
            await _show_service_detail(update, context, service_id)
        except Exception as exc:
            logger.error("Callback svc detail error: %s", exc, exc_info=True)
            await query.answer("❌ Terjadi kesalahan.")

    elif data.startswith("svcnum_"):
        # User menekan tombol angka — resolve posisi ke service_id
        try:
            num = int(data[7:])  # strip "svcnum_"
            svc_list_ids: list = context.user_data.get("svc_list_ids", [])
            if not svc_list_ids or num < 1 or num > len(svc_list_ids):
                await query.answer("❌ Nomor tidak valid.")
                return
            service_id = svc_list_ids[num - 1]
            await _show_service_detail(update, context, service_id)
        except Exception as exc:
            logger.error("Callback svcnum error: %s", exc, exc_info=True)
            await query.answer("❌ Terjadi kesalahan.")

    elif data == "cmd_history":
        await handle_history(update, context)

    elif data.startswith("cekorder_"):
        try:
            order_id = int(data.split("_", 1)[1])
            await _show_order(update, context, tg_user.id, tg_user.username, order_id)
        except Exception as exc:
            logger.error("Callback cekorder error: %s", exc, exc_info=True)
            await query.message.reply_text("❌ Terjadi kesalahan.")

    else:
        logger.warning("Unknown callback data: %s", data)
