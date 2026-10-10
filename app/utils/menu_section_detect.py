"""Автодетект секции по клавиатуре сообщения — для показа баннера раздела.

Правила упорядочены: первое совпадение по любому callback_data кнопок выигрывает.
Если ни одно правило не сработало — возвращает None (экран покажет общий логотип).
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

_INFO_COMBO = {
    'menu_faq',
    'menu_rules',
    'menu_privacy_policy',
    'menu_public_offer',
    'menu_server_status',
    'menu_info_promo_groups',
}

# (section_key, exact_matches, prefix_matches)
_RULES: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    ('faq', frozenset({'menu_faq'}), ('menu_faq_page:',)),
    ('privacy_policy', frozenset(), ('menu_privacy_policy:',)),
    ('public_offer', frozenset(), ('menu_public_offer:',)),
    ('server_status', frozenset(), ('server_status_page:',)),
    ('language', frozenset(), ('language_select:',)),
    ('referrals', frozenset(), ('referral_',)),
    (
        'support',
        frozenset(),
        ('create_ticket', 'my_tickets', 'view_ticket_', 'reply_ticket_', 'close_ticket', 'ticket_attachments_'),
    ),
    ('trial', frozenset({'trial_activate'}), ('trial_payment',)),
    (
        'subscription',
        frozenset(
            {
                'my_subscriptions',
                'subscription',
                'buy_traffic',
                'open_subscription_link',
                'subscription_settings',
                'subscription_extend',
                'subscription_autopay',
                'subscription_upgrade',
                'subscription_revoke',
                'subscription_cancel',
                'subscription_manage_devices',
                'subscription_reset_traffic',
                'subscription_reset_devices',
                'subscription_change_devices',
                'subscription_switch_traffic',
                'subscription_add_countries',
                'subscription_gift',
                'toggle_daily_subscription_pause',
                'confirm_reset_traffic',
                'confirm_reset_devices',
                'reset_all_devices',
                'devices_continue',
                'countries_apply',
                'countries_continue',
            }
        ),
        (
            'sm:',
            'se:',
            'sl:',
            'sd:',
            'sr:',
            'st:',
            'sub_del',
            'autopay',
            'extend_period_',
            'add_traffic_',
            'switch_traffic_',
            'confirm_switch_traffic_',
            'add_devices_',
            'confirm_add_devices_',
            'change_devices',
            'device_management',
            'device_rename',
            'devices_',
            'reset_device_',
            'subscription_connect:',
            'subscription_revoke',
            'country_',
            'custom_confirm:',
        ),
    ),
    (
        'balance',
        frozenset({'balance_topup'}),
        ('balance_history', 'saved_cards', 'unlink_card_'),
    ),
    (
        'payment',
        frozenset({'payment_methods_unavailable'}),
        (
            'topup_',
            'check_',
            'pal24_method',
            'platega_method',
            'simple_subscription_',
            'payment_methods',
            'yookassa',
        ),
    ),
    (
        'subscription_purchase',
        frozenset({'buy_subscription_tariffs'}),
        (
            'tariff_',
            'custom_days:',
            'custom_traffic:',
            'daily_tariff',
            'instant_sw',
            'period_',
        ),
    ),
    ('promocode', frozenset(), ('promo_sub:',)),
)


def _extract_callbacks(reply_markup) -> set[str]:
    callbacks: set[str] = set()
    if not isinstance(reply_markup, InlineKeyboardMarkup):
        return callbacks
    try:
        inline = getattr(reply_markup, 'inline_keyboard', None) or []
    except Exception:
        return callbacks
    for row in inline:
        for button in row:
            data = getattr(button, 'callback_data', None)
            if data:
                callbacks.add(data)
    return callbacks


def detect_section_key(reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None) -> str | None:
    """Определяет секцию по callback_data кнопок клавиатуры. None — секция не распознана."""
    callbacks = _extract_callbacks(reply_markup)
    if not callbacks:
        return None

    # Экран «Информация»: несколько info-кнопок одновременно — до индивидуальных правил,
    # иначе кнопка menu_rules на инфо-клавиатуре утащила бы экран в «Правила».
    if len(_INFO_COMBO & callbacks) >= 2:
        return 'info'

    for section, exact, prefixes in _RULES:
        if exact & callbacks:
            return section
        for prefix in prefixes:
            if any(cb.startswith(prefix) for cb in callbacks):
                return section
    return None
