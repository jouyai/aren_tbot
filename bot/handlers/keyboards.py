"""
Keyboard builders untuk ReplyKeyboardMarkup (keyboard di area input).

Ini adalah keyboard yang muncul di bawah input pesan — bukan inline di dalam pesan.
User bisa tap tombol seperti menekan teks biasa.
"""
from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove


def kb_main_reply() -> ReplyKeyboardMarkup:
    """Keyboard utama yang selalu tampil setelah /start."""
    return ReplyKeyboardMarkup(
        [
            ["🛒 Layanan", "💰 Saldo"],
            ["💳 Top Up", "📜 Riwayat"],
            ["👤 Profil"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Pilih menu atau ketik perintah...",
    )


def kb_categories_reply(categories: list[str]) -> ReplyKeyboardMarkup:
    """Keyboard kategori layanan — 3 per baris."""
    rows = []
    row = []
    for cat in categories:
        row.append(cat)
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # Baris navigasi di bawah
    rows.append(["📋 Semua Produk", "🏠 Menu Utama"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def kb_service_numbers_reply(service_ids: list[int]) -> ReplyKeyboardMarkup:
    """Keyboard nomor layanan — 7 per baris seperti contoh."""
    rows = []
    row = []
    for sid in service_ids:
        row.append(str(sid))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(["📋 List Produk", "🏠 Menu Utama"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def kb_topup_amounts_reply() -> ReplyKeyboardMarkup:
    """Keyboard nominal top up."""
    return ReplyKeyboardMarkup(
        [
            ["Rp 10.000", "Rp 25.000", "Rp 50.000"],
            ["Rp 100.000", "Rp 250.000", "Rp 500.000"],
            ["Rp 1.000.000", "Rp 2.000.000", "Rp 5.000.000"],
            ["🏠 Menu Utama"],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Pilih nominal atau ketik jumlah...",
    )


def kb_remove() -> ReplyKeyboardRemove:
    """Hapus keyboard (kembali ke keyboard default Telegram)."""
    return ReplyKeyboardRemove()
