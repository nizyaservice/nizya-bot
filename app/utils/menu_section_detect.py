"""Автодетект секции по клавиатуре сообщения — для показа баннера раздела.

Алгоритм:
1. Каждая кнопка клавиатуры матчится в свой ключ (подпункт специфичнее раздела).
2. Один уникальный ключ у всех кнопок → это экран подпункта/раздела.
3. Несколько ключей, но все из одного раздела → главный экран раздела
   (у него кнопки-ссылки на несколько подпунктов сразу).
4. Кнопки из разных разделов (навигационные экраны) → None → общий логотип.
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

# Подпункты: специфичнее разделов, матчатся первыми.
_SUBRULES: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    # Флоу покупки подписки (в т.ч. «простая покупка») — до balance.topup,
    # чтобы simple_subscription_*/check_simple_* не утаскивали экран покупки в пополнение.
    ('subscription_purchase', frozenset(), ('simple_subscription_', 'check_simple_')),
    # Кнопки «Проверить оплату» триала — экран остаётся экраном триала.
    ('trial', frozenset(), ('check_trial_',)),
    # balance_topup — кнопка именно пополнения (не главный экран раздела), иначе инвойсы
    # и экраны нехватки средств получают баннер раздела вместо пополнения.
    # menu_balance — навигационная кнопка «Баланс» из главного меню: корень balance,
    # а не подпункт пополнения (иначе смесь с menu_buy/menu_referrals на главном меню
    # попадает в спец-кейс нехватки средств и даёт баннер пополнения).
    ('balance.topup', frozenset({'payment_methods_unavailable', 'balance_topup'}), (
        'topup_',
        'check_',
        'pal24_method',
        'platega_method',
        'payment_methods',
        'yookassa',
    )),
    ('balance.history', frozenset(), ('balance_history',)),
    ('balance.cards', frozenset(), ('saved_cards', 'unlink_card_')),
    (
        'subscription.devices',
        frozenset({'reset_all_devices', 'confirm_reset_devices', 'devices_continue'}),
        (
            'device_management',
            'change_devices',
            'devices_',
            'add_devices_',
            'confirm_add_devices_',
            'reset_device_',
            'device_rename',
        ),
    ),
    (
        'subscription.traffic',
        frozenset({'buy_traffic', 'confirm_reset_traffic'}),
        (
            'add_traffic_',
            'switch_traffic_',
            'confirm_switch_traffic_',
            'subscription_switch_traffic',
            'subscription_reset_traffic',
        ),
    ),
    ('subscription.autopay', frozenset(), ('autopay', 'subscription_autopay', 'sbp_recurring')),
    ('subscription.extend', frozenset({'subscription_extend'}), ('extend_period_', 'tariff_ext')),
    ('subscription.revoke', frozenset({'subscription_revoke', 'subscription_revoke_confirm'}), ('sub_del_yes', 'sr:')),
    ('referral.list', frozenset(), ('referral_list',)),
    ('referral.analytics', frozenset(), ('referral_analytics',)),
    ('referral.withdrawal', frozenset(), ('referral_withdrawal',)),
)

# (section_key, exact_matches, prefix_matches)
_RULES: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    ('faq', frozenset({'menu_faq'}), ('menu_faq_page:',)),
    ('privacy_policy', frozenset({'privacy_policy_accept', 'privacy_policy_decline'}), ('menu_privacy_policy:',)),
    ('public_offer', frozenset(), ('menu_public_offer:',)),
    ('server_status', frozenset(), ('server_status_page:',)),
    ('language', frozenset(), ('language_select:',)),
    # Кастомные инфо-страницы (info_page:id:page) — та же секция «Инфо».
    ('info', frozenset(), ('info_page:',)),
    # Многостраничные «Правила сервиса» (menu_rules:page) и шаг регистрации (rules_accept/decline).
    ('rules', frozenset({'rules_accept', 'rules_decline'}), ('menu_rules:',)),
    ('referrals', frozenset({'menu_referrals'}), ('referral_',)),
    (
        'support',
        frozenset({'menu_support'}),
        (
            'create_ticket',
            'my_tickets',
            'view_ticket_',
            'reply_ticket_',
            'close_ticket',
            'ticket_attachments_',
            'cancel_ticket',
            'user_delete_message_',
        ),
    ),
    ('trial', frozenset({'trial_activate'}), ('trial_payment',)),
    (
        'subscription',
        frozenset(
            {
                'my_subscriptions',
                'subscription',
                'menu_subscription',
                'open_subscription_link',
                'subscription_settings',
                'subscription_upgrade',
                'subscription_gift',
                'subscription_add_countries',
                'toggle_daily_subscription_pause',
                'countries_apply',
                'countries_continue',
            }
        ),
        (
            'sm:',
            'se:',
            'sl:',
            'sd:',
            'st:',
            'subscription_connect:',
            'subscription_manage_devices',
            'subscription_change_devices',
            # Флоу подарков (gift_*) живёт внутри раздела подписок.
            'gift_',
            # Экран «Тариф» (смена тарифа) — подраздел подписок, не покупки.
            'instant_sw',
        ),
    ),
    # Кнопка «Баланс» главного меню — раздел баланса (инвойсы платёжек с back-кнопкой
    # menu_balance тоже получают баннер раздела, картинка у topup/cards/history общая).
    ('balance', frozenset({'menu_balance'}), ()),
    (
        'subscription_purchase',
        frozenset({'buy_subscription_tariffs', 'subscription_purchase', 'subscription_confirm', 'menu_buy'}),
        (
            'tariff_',
            'custom_days:',
            'custom_traffic:',
            'custom_confirm:',
            'daily_tariff',
            'period_',
            # Шаг выбора пакета трафика в чекауте покупки.
            'traffic_',
        ),
    ),
    ('promocode', frozenset(), ('promo_sub:',)),
)

_ALL_RULES = _SUBRULES + _RULES

# Подсекции 'referral.*' живут в разделе с ключом 'referrals' — приводим корни.
_PARENT_ROOT_ALIAS = {'referral': 'referrals'}


def _root_section(key: str) -> str:
    root = key.split('.', 1)[0]
    return _PARENT_ROOT_ALIAS.get(root, root)


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


def _match_callback(callback: str) -> str | None:
    for section, exact, prefixes in _ALL_RULES:
        if callback in exact:
            return section
        for prefix in prefixes:
            if callback.startswith(prefix):
                return section
    return None


def detect_section_key(reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None) -> str | None:
    """Ключ баннера по клавиатуре: подпункт, раздел или None (общий логотип)."""
    callbacks = _extract_callbacks(reply_markup)
    if not callbacks:
        return None

    # Экран «Информация»: несколько info-кнопок одновременно — до индивидуальных правил.
    if len(_INFO_COMBO & callbacks) >= 2:
        return 'info'

    keys = {key for key in (_match_callback(cb) for cb in callbacks) if key}
    if not keys:
        return None
    if len(keys) == 1:
        return next(iter(keys))

    parents = {_root_section(key) for key in keys}
    if len(parents) == 1:
        root = next(iter(parents))
        # Кнопки подпунктов этого раздела среди ключей. Экран подпункта
        # («список рефералов» + back «в партнёрку») содержит ОДИН подпункт
        # и back-кнопку раздела — баннер подпункта. Главный экран раздела
        # содержит кнопки НЕСКОЛЬКИХ подпунктов — баннер раздела.
        subkeys = {k for k in keys if _root_section(k) == root and k != root}
        if len(subkeys) == 1:
            return next(iter(subkeys))
        return root
    # Экран нехватки средств: кнопки пополнения + возврат во флоу покупки/подписки
    # (balance_topup + tariff_* / subscription_*). Показываем баннер пополнения,
    # а не общий логотип.
    if 'balance.topup' in keys:
        return 'balance.topup'
    return None
