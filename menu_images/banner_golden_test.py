#!/usr/bin/env python3
"""Золотой тест баннеров разделов: фактический баннер == ожидаемому на каждом экране аудита.

Модель фактического баннера (как в продакшене, app/utils/photo_message.py + message_patch.py):
  factual = media-ключ хендлера (явный media= отключает автодетект) ЛИБО detect_section_key(клавиатура).
Картинка секции определяется через resolve_cached_section_file_id(): подпункт без своей
картинки -> картинка родителя, затем алиас (subscription -> my_subscriptions, referral -> referrals).
Поэтому сравнение ведётся по ключу картинки: eff() применяет тот же фолбэк к обеим сторонам.

Кейсы — из аудита экранов (имя, callbacks, expected, media). media — ключ секции,
который хендлер передаёт в get_section_media_kwargs(...)/answer_photo (см. правки хендлеров).

Запуск: python3 menu_images/banner_golden_test.py; exit code 0 — все кейсы зелёные.
"""

import importlib.util
import sys
import types

t = types.ModuleType('aiogram.types')
aiogram = types.ModuleType('aiogram')


class IKM:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


class RKM:
    pass


class B:
    def __init__(self, cd):
        self.callback_data = cd


t.InlineKeyboardMarkup = IKM
t.ReplyKeyboardMarkup = RKM
sys.modules['aiogram'] = aiogram
sys.modules['aiogram.types'] = t
spec = importlib.util.spec_from_file_location(
    'msd',
    '/home/klimy/Документы/ApertuRU_VPN/bedolaga-live/app/utils/menu_section_detect.py',
)
msd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(msd)


def kb(*cbs):
    return IKM([[B(c)] for c in cbs])


def detect(*cbs):
    return msd.detect_section_key(kb(*cbs))


# Ключи секций с собственной картинкой в БД (SECTION_IMAGE_DEFINITIONS).
DEFINED = {
    'main_menu',
    'profile',
    'balance',
    'topup_success',
    'my_subscriptions',
    'active_subscription',
    'promocode',
    'support',
    'referrals',
    'subscription_purchase',
    'subscription_upgrade',
    'subscription_success',
    'info',
    'faq',
    'rules',
    'privacy_policy',
    'public_offer',
    'language',
    'server_status',
    'trial',
    # Подпункты — у каждого своя картинка (32 ключа в БД), НЕ схлопывать.
    'balance.history',
    'balance.cards',
    'balance.topup',
    'subscription.settings',
    'subscription.devices',
    'subscription.traffic',
    'subscription.autopay',
    'subscription.extend',
    'subscription.revoke',
    'referral.list',
    'referral.analytics',
    'referral.withdrawal',
}

# en-варианты баннеров (ui_section_image.<key>.en — русские мелкие подписи
# для англоязычного интерфейса, инверсия языков по дизайну владельца; 64 ключа
# в БД). В автодетекте не участвуют (там языка нет — базовый вариант); eff()
# их не схлопывает — это самостоятельные картинки, а не иерархический фолбэк.
DEFINED_EN = {f'{k}.en' for k in tuple(DEFINED)}
assert len(DEFINED_EN) == 32, len(DEFINED_EN)
assert not (DEFINED_EN & DEFINED)
DEFINED |= DEFINED_EN
assert len(DEFINED) == 64, len(DEFINED)
# _SECTION_IMAGE_ALIASES из app/services/section_image_service.py
ALIAS = {'subscription': 'my_subscriptions', 'referral': 'referrals'}


def eff(k):
    """Ключ фактической картинки: подпункт без своей картинки -> родитель -> алиас."""
    if k is None:
        return None
    if k not in DEFINED and '.' in k:
        k = k.rsplit('.', 1)[0]
    return ALIAS.get(k, k)


# (name, callbacks, expected, media|None)  — media = ключ явного media= в хендлере
C = []


def case(name, cbs, expected, media=None):
    C.append((name, cbs, expected, media))


# ===== menu.py =====
case(
    'main_menu',
    [
        'subscription_connect',
        'menu_subscription',
        'buy_traffic',
        'menu_balance',
        'menu_trial',
        'menu_buy',
        'simple_subscription_purchase',
        'menu_promocode',
        'menu_referrals',
        'contests_menu',
        'menu_support',
        'activate_button',
        'menu_info',
        'menu_language',
        'admin_panel',
    ],
    'main_menu',
    'main_menu',
)
case(
    'info_menu',
    [
        'menu_faq',
        'menu_info_promo_groups',
        'menu_privacy_policy',
        'menu_public_offer',
        'menu_rules',
        'info_page:7:1',
        'info_page:8:1',
        'menu_server_status',
        'back_to_menu',
    ],
    'info',
)
case('promo_groups_empty', ['menu_info'], 'info', 'info')
case('promo_groups_filled', ['menu_info'], 'info', 'info')
case('faq_list', ['menu_faq_page:3:1', 'menu_faq_page:5:1', 'menu_faq_page:9:1', 'menu_info'], 'faq')
case('faq_page_paginated', ['menu_faq_page:3:2', 'noop', 'menu_faq', 'menu_info'], 'faq')
case('faq_page_single', ['menu_faq', 'menu_info'], 'faq')
case('service_rules_pages', ['menu_rules:1', 'menu_rules:2', 'noop', 'back_to_menu'], 'rules')
case('service_rules_single', ['back_to_menu'], 'rules', 'rules')
case('privacy_multi', ['menu_privacy_policy:1', 'noop', 'menu_info'], 'privacy_policy')
case('privacy_single', ['menu_info'], 'privacy_policy', 'privacy_policy')
case('offer_multi', ['menu_public_offer:1', 'noop', 'menu_info'], 'public_offer')
case('offer_single', ['menu_info'], 'public_offer', 'public_offer')
case('info_page_custom', ['info_page:7:2', 'noop', 'menu_info'], 'info')
case(
    'language_menu',
    [
        'language_select:ru',
        'language_select:en',
        'language_select:ua',
        'language_select:zh',
        'language_select:fa',
        'back_to_menu',
    ],
    'language',
)

# ===== purchase.py / tariff_purchase.py =====
case('trial_offer', ['trial_activate', 'back_to_menu'], 'trial', 'trial')
case('trial_unavailable', ['back_to_menu'], 'trial', 'trial')
case('trial_restriction', ['subscription'], 'trial', 'trial')
case(
    'trial_payment_methods',
    [
        'trial_pay_with_balance',
        'trial_payment_stars',
        'trial_payment_yookassa_sbp',
        'trial_payment_yookassa',
        'trial_payment_cryptobot',
        'trial_payment_heleket',
        'trial_payment_mulenpay',
        'trial_payment_pal24',
        'trial_payment_wata',
        'trial_payment_platega',
        'menu_trial',
    ],
    'trial',
)
case('trial_insufficient', ['topup_amount|stars|15000', 'topup_amount|yookassa|15000', 'back_to_menu'], 'balance.topup')
case('trial_success_connect', ['subscription_connect', 'subscription_happ_download', 'back_to_menu'], 'trial', 'trial')
case('trial_errors_1', ['back_to_menu'], 'trial', 'trial')
case('trial_errors_2', ['back_to_menu'], 'trial', 'trial')
case('trial_pay_balance_success', ['subscription_connect', 'back_to_menu'], 'trial', 'trial')
case('trial_stars_waiting', ['back_to_menu'], 'trial', 'trial')
case('trial_yookassa_back', ['trial_activate'], 'trial')
case('trial_check_crypto', ['check_trial_cryptobot_42', 'trial_activate'], 'trial')
case('purchase_period', ['period_30', 'period_90', 'period_180', 'period_365', 'back_to_menu'], 'subscription_purchase')
case(
    'purchase_traffic_step',
    ['traffic_50', 'traffic_100', 'traffic_0', 'subscription_config_back'],
    'subscription_purchase',
)
case(
    'purchase_countries_step',
    ['country_9f2c', 'country_1ab3', 'countries_continue', 'subscription_config_back'],
    'subscription_purchase',
    'subscription_purchase',
)
case(
    'purchase_devices_step',
    ['devices_1', 'devices_2', 'devices_3', 'devices_4', 'devices_continue', 'subscription_config_back'],
    'subscription_purchase',
    'subscription_purchase',
)
case('purchase_summary', ['subscription_confirm', 'subscription_cancel'], 'subscription_purchase')
case('purchase_restriction', ['subscription'], 'subscription_purchase', 'subscription_purchase')
case(
    'purchase_insufficient',
    ['topup_amount|stars|20000', 'topup_amount|yookassa|20000', 'back_to_menu', 'subscription_resume_checkout'],
    'balance.topup',
)
case(
    'purchase_success',
    ['subscription_connect', 'subscription_happ_download', 'back_to_menu'],
    'subscription_success',
    'subscription_success',
)
case('purchase_errors', ['back_to_menu'], 'subscription_purchase', 'subscription_purchase')
case('resume_checkout', ['subscription_confirm', 'subscription_cancel'], 'subscription_purchase')
case(
    'cart_still_insufficient',
    [
        'clear_saved_cart',
        'topup_amount|stars|5000',
        'topup_amount|yookassa|5000',
        'return_to_saved_cart',
        'back_to_menu',
    ],
    'balance.topup',
)
case(
    'cart_restored_confirm',
    ['subscription_confirm', 'clear_saved_cart', 'subscription_config_back'],
    'subscription_purchase',
)
case(
    'cart_topup_methods',
    [
        'topup_amount|stars|7000',
        'topup_amount|yookassa|7000',
        'menu_balance',
        'clear_saved_cart',
        'return_to_saved_cart',
    ],
    'balance.topup',
)
case('extend_no_sub', ['menu_buy', 'webhook:close'], 'subscription_purchase')
case('extend_legacy', ['tariff_switch', 'menu_subscription'], 'subscription_purchase', 'subscription_purchase')
case(
    'extend_periods',
    ['extend_period_30', 'extend_period_90', 'extend_period_180', 'menu_subscription'],
    'subscription.extend',
    'subscription.extend',
)
case('extend_insufficient', ['topup_amount|stars|9900', 'back_to_menu', 'return_to_saved_cart'], 'balance.topup')
case('extend_success', ['back_to_menu'], 'subscription.extend', 'subscription.extend')
case(
    'simple_purchase_ok',
    ['simple_subscription_pay_with_balance', 'simple_subscription_other_payment_methods', 'subscription_purchase'],
    'subscription_purchase',
)
case(
    'simple_purchase_methods',
    [
        'simple_subscription_stars',
        'simple_subscription_yookassa_sbp',
        'simple_subscription_yookassa',
        'simple_subscription_cryptobot',
        'simple_subscription_heleket',
        'simple_subscription_mulenpay',
        'simple_subscription_pal24',
        'simple_subscription_wata',
        'subscription_purchase',
    ],
    'subscription_purchase',
)
case('simple_extend_insufficient', ['topup_amount|stars|3000', 'back_to_menu', 'return_to_saved_cart'], 'balance.topup')
case('simple_extend_success', ['back_to_menu'], 'subscription.extend', 'subscription.extend')
case('my_subs_no_sub', ['subscription_gift', 'back_to_menu'], 'my_subscriptions', 'my_subscriptions')
case(
    'my_subs_classic',
    [
        'subscription_connect',
        'subscription_extend',
        'subscription_autopay',
        'subscription_settings',
        'subscription_gift',
        'back_to_menu',
    ],
    'my_subscriptions',
)
case(
    'my_subs_tariff_mode',
    [
        'subscription_connect',
        'subscription_extend',
        'subscription_autopay',
        'subscription_settings',
        'instant_switch',
        'subscription_gift',
        'back_to_menu',
    ],
    'my_subscriptions',
    'my_subscriptions',
)
case(
    'subscription_settings',
    [
        'subscription_add_countries',
        'subscription_reset_traffic',
        'subscription_switch_traffic',
        'subscription_change_devices',
        'subscription_manage_devices',
        'subscription_revoke',
        'menu_subscription',
    ],
    'subscription.settings',
)
case(
    'tariffs_list',
    ['tariff_select:1', 'tariff_select:2', 'tariff_select:3', 'back_to_menu'],
    'subscription_purchase',
    'subscription_purchase',
)
case('tariffs_empty', ['back_to_menu'], 'subscription_purchase', 'subscription_purchase')
case(
    'daily_confirm',
    ['daily_tariff_confirm:5', 'tariff_sbp:5', 'tariff_lava:5', 'tariff_cashera:5', 'menu_buy'],
    'subscription_purchase',
)
case(
    'daily_insufficient',
    ['balance_topup', 'tariff_sbp:5', 'tariff_lava:5', 'tariff_cashera:5', 'menu_buy'],
    'balance.topup',
)
case(
    'custom_tariff_full',
    [
        'custom_days:1:-30',
        'custom_days:1:-7',
        'custom_days:1:-1',
        'noop',
        'custom_days:1:1',
        'custom_days:1:7',
        'custom_days:1:30',
        'custom_traffic:1:-100',
        'custom_traffic:1:-10',
        'custom_traffic:1:-1',
        'custom_traffic:1:1',
        'custom_traffic:1:10',
        'custom_traffic:1:100',
        'custom_confirm:1',
        'menu_buy',
    ],
    'subscription_purchase',
)
case(
    'custom_tariff_traffic',
    [
        'noop',
        'custom_traffic:1:-10',
        'custom_traffic:1:-1',
        'custom_traffic:1:1',
        'custom_traffic:1:10',
        'custom_confirm:1',
        'menu_buy',
    ],
    'subscription_purchase',
)
case(
    'custom_tariff_rerender',
    ['custom_days:1:1', 'custom_traffic:1:10', 'custom_confirm:1', 'menu_buy'],
    'subscription_purchase',
)
case(
    'tariff_periods',
    ['tariff_period:1:30', 'tariff_period:1:90', 'tariff_period:1:180', 'menu_buy'],
    'subscription_purchase',
)
case(
    'tariff_periods_traffic',
    ['tariff_period_traffic:1:30', 'tariff_period_traffic:1:90', 'menu_buy'],
    'subscription_purchase',
)
case(
    'tariff_confirm',
    ['tariff_confirm:1:30', 'tariff_sbp:1', 'tariff_lava:1', 'tariff_cashera:1', 'tariff_select:1'],
    'subscription_purchase',
)
case('tariff_insufficient', ['balance_topup', 'tariff_sbp:1', 'tariff_select:1'], 'balance.topup')
case('tariff_insufficient_auto', ['topup_amount|stars|5000', 'tariff_sbp:1'], 'balance.topup')
case('tariff_success', ['sm:12', 'back_to_menu'], 'subscription_success', 'subscription_success')
case('tariff_errors', [], 'subscription_purchase', 'subscription_purchase')
case('custom_success', ['sm:12', 'back_to_menu'], 'subscription_success', 'subscription_success')
case('daily_success', ['sm:12', 'back_to_menu'], 'subscription_success', 'subscription_success')
case('tariff_extend_picker', ['se:12', 'se:13', 'back_to_menu'], 'subscription.extend', 'subscription.extend')
case('tariff_extend_legacy', ['tariff_select:1', 'tariff_select:2', 'back_to_menu'], 'subscription_purchase')
case(
    'tariff_extend_main',
    ['tariff_extend:12:1:30', 'tariff_extend:12:1:90', 'tariff_extend:12:1:180', 'menu_subscription'],
    'subscription.extend',
    'subscription.extend',
)
case('tariff_extend_confirm', ['tariff_ext_confirm:12:1:30', 'subscription_extend'], 'subscription.extend')
case('tariff_extend_insufficient', ['balance_topup', 'subscription_extend'], 'balance.topup')
case('tariff_extend_insufficient_auto', ['topup_amount|stars|5000', 'se:12'], 'balance.topup')
case('tariff_extend_success', ['sm:12', 'back_to_menu'], 'subscription.extend', 'subscription.extend')
case(
    'switch_list',
    ['tariff_sw_select:2', 'tariff_sw_select:3', 'menu_subscription'],
    'subscription_purchase',
    'subscription_purchase',
)
case('switch_expired', ['menu_buy', 'menu_subscription'], 'subscription_purchase', 'subscription_purchase')
case('switch_unavailable', ['menu_subscription'], 'subscription_purchase', 'subscription_purchase')
case('daily_switch_confirm', ['daily_tariff_switch_confirm:5', 'tariff_switch'], 'subscription_purchase')
case('daily_switch_insufficient', ['balance_topup', 'tariff_switch'], 'balance.topup')
case('switch_periods', ['tariff_sw_period:2:30', 'tariff_sw_period:2:90', 'tariff_switch'], 'subscription_purchase')
case('switch_confirm', ['tariff_sw_confirm:2:30', 'tariff_sw_select:2'], 'subscription_purchase')
case('switch_insufficient', ['balance_topup', 'tariff_sw_select:2'], 'balance.topup')
case('switch_success', ['sm:12', 'back_to_menu'], 'subscription_success', 'subscription_success')
case('daily_switch_success', ['sm:12', 'back_to_menu'], 'subscription_success', 'subscription_success')
case(
    'instant_list',
    ['instant_sw_preview:2', 'instant_sw_preview:3', 'menu_subscription'],
    'subscription_purchase',
    'subscription_purchase',
)
case('instant_expired', ['menu_buy', 'menu_subscription'], 'subscription_purchase', 'subscription_purchase')
case(
    'instant_confirm', ['instant_sw_confirm:2', 'instant_switch'], 'subscription'
)  # экран «Тариф» — подраздел подписок
case('instant_insufficient', ['balance_topup', 'instant_switch'], 'balance.topup')
case('instant_success', ['sm:12', 'back_to_menu'], 'subscription_success', 'subscription_success')
case('sbp_autorenew', ['tariff_list'], 'subscription_purchase')
case('lava_autorenew', ['tariff_list'], 'subscription_purchase')
case('cashera_autorenew', ['tariff_list'], 'subscription_purchase')
case('restored_purchase_cart', ['tariff_confirm:1:30', 'tariff_sbp:1', 'tariff_select:1'], 'subscription_purchase')
case('restored_daily_cart', ['daily_tariff_confirm:5', 'menu_buy'], 'subscription_purchase')
case('restored_extend_cart', ['tariff_ext_confirm:12:1:30', 'tariff_extend:12:1'], 'subscription.extend')
case('restored_insufficient', ['balance_topup', 'tariff_sbp:1', 'tariff_select:1'], 'balance.topup')

# ===== my_subscriptions / links / summary =====
case(
    'my_subs_list',
    ['sm:1', 'sm:2', 'menu_buy', 'subscription_gift', 'back_to_menu'],
    'my_subscriptions',
    'my_subscriptions',
)
case('my_subs_empty', ['menu_buy', 'subscription_gift', 'back_to_menu'], 'my_subscriptions', 'my_subscriptions')
case(
    'detail_active',
    ['sl:5', 'se:5', 'subscription_autopay', 'st:5', 'sd:5', 'sr:5', 'my_subscriptions'],
    'active_subscription',
    'active_subscription',
)
case('detail_inactive', ['se:5', 'sub_del:5', 'my_subscriptions'], 'active_subscription', 'active_subscription')
case('subs_devices_menu', ['change_devices_menu:5', 'device_management:5', 'sm:5'], 'subscription.devices')
case('delete_confirm', ['sub_del_yes:5', 'sm:5'], 'subscription.revoke')
# Экран «Настройки подписки»: manage_devices + перевыпуск + назад — баннер настроек.
case(
    'subs_settings_tariff',
    ['subscription_manage_devices', 'subscription_revoke', 'menu_subscription'],
    'subscription.settings',
)
case('connect_multi', ['sl:1', 'sl:2', 'back_to_menu'], 'my_subscriptions')
case('connect_miniapp_sub', ['sm:5'], 'my_subscriptions')
case('connect_miniapp_custom', ['sm:5'], 'my_subscriptions')
case('connect_link', ['subscription_happ_download', 'sm:5'], 'my_subscriptions')
case('connect_happ', ['open_subscription_link:5', 'subscription_happ_download', 'sm:5'], 'my_subscriptions')
case(
    'connect_guide',
    [
        'device_guide_ios',
        'device_guide_android',
        'device_guide_macos',
        'device_guide_windows',
        'open_subscription_link:5',
        'sm:5',
    ],
    'my_subscriptions',
)
case('connect_guide_error', ['sm:5'], 'my_subscriptions')
case(
    'happ_crypto_branch',
    ['happ_download_ios', 'happ_download_android', 'happ_download_macos', 'happ_download_windows', 'back_to_menu'],
    'my_subscriptions',
    'my_subscriptions',
)
case('connect_normal', ['subscription_connect:5', 'sm:5'], 'my_subscriptions')
case('summary_confirm', ['subscription_confirm', 'subscription_cancel'], 'subscription_purchase')

# ===== traffic/devices/autopay/revoke/gift/countries/promo =====
case('add_traffic_pick', ['st:1', 'st:2', 'back_to_menu'], 'subscription')
case(
    'add_traffic_tariff', ['add_traffic_50', 'add_traffic_100', 'sm:3'], 'subscription.traffic', 'subscription.traffic'
)
case(
    'add_traffic_global',
    ['add_traffic_50', 'add_traffic_100', 'menu_subscription'],
    'subscription.traffic',
    'subscription.traffic',
)
case('add_traffic_empty', ['no_traffic_packages', 'menu_subscription'], 'subscription.traffic', 'subscription.traffic')
case('reset_confirm', ['confirm_reset_traffic', 'subscription_settings'], 'subscription.traffic')
case('reset_insufficient', ['balance_topup', 'subscription_settings'], 'balance.topup')
case(
    'reset_insufficient_topup',
    ['topup_amount|stars|5000', 'topup_amount|yookassa|5000', 'back_to_menu'],
    'balance.topup',
)
case('reset_success', ['back_to_menu'], 'subscription.traffic', 'subscription.traffic')
case('add_traffic_insufficient_cart', ['topup_amount|stars|5000', 'back_to_menu'], 'balance.topup')
case('add_traffic_success', ['back_to_menu'], 'subscription.traffic', 'subscription.traffic')
case(
    'switch_traffic_choice',
    ['switch_traffic_50', 'switch_traffic_0', 'sm:3', 'subscription_settings'],
    'subscription.traffic',
)
case('switch_traffic_confirm', ['confirm_switch_traffic_50_1000', 'subscription_settings'], 'subscription.traffic')
case('switch_traffic_insufficient', ['topup_amount|stars|5000', 'back_to_menu'], 'balance.topup')
case('switch_traffic_success', ['back_to_menu'], 'subscription.traffic', 'subscription.traffic')
case(
    'change_devices_choice',
    ['change_devices_3', 'change_devices_4', 'subscription_settings', 'sm:5'],
    'subscription.devices',
)
case(
    'change_devices_confirm',
    ['confirm_change_devices_4_1000', 'subscription_settings'],
    'subscription.devices',
    'subscription.devices',
)
case(
    'change_devices_insufficient', ['topup_amount|stars|5000', 'return_to_saved_cart', 'back_to_menu'], 'balance.topup'
)
case('change_devices_success', ['back_to_menu'], 'subscription.devices', 'subscription.devices')
case(
    'device_management',
    [
        'device_rename_0_1',
        'reset_device_0_1',
        'devices_page_2',
        'current_page',
        'reset_all_devices',
        'sm:5',
        'subscription_settings',
    ],
    'subscription.devices',
)
case('device_mgmt_empty', ['back_to_menu'], 'subscription.devices', 'subscription.devices')
case('rename_prompt', ['device_rename_cancel'], 'subscription.devices')
case('all_reset_final', ['back_to_menu'], 'subscription.devices', 'subscription.devices')
case('add_devices_insufficient', ['topup_amount|stars|5000', 'return_to_saved_cart', 'back_to_menu'], 'balance.topup')
case('add_devices_success', ['back_to_menu'], 'subscription.devices', 'subscription.devices')
case('device_guide', ['app_list_ios', 'subscription_connect', 'menu_subscription'], 'subscription')
case(
    'app_selection', ['app_ios_v2ray', 'app_ios_streisand', 'subscription_connect', 'menu_subscription'], 'subscription'
)
case('specific_app_guide', ['app_list_ios', 'subscription_connect:5', 'sm:5'], 'subscription')
case('connection_help', ['device_connection_help', 'subscription_manage_devices', 'menu_subscription'], 'subscription.settings')
case(
    'autopay_menu',
    [
        'autopay_enable',
        'autopay_disable',
        'autopay_set_days',
        'autopay_set_period',
        'sbp_recurring_menu',
        'menu_subscription',
    ],
    'subscription.autopay',
)
case('autopay_daily', ['sbp_recurring_menu', 'cashera_recurring_cancel', 'menu_subscription'], 'subscription.autopay')
case(
    'autopay_days',
    ['autopay_days_1', 'autopay_days_3', 'autopay_days_7', 'autopay_days_14', 'subscription_autopay'],
    'subscription.autopay',
)
case('autopay_period', ['autopay_period_default', 'autopay_period_30', 'subscription_autopay'], 'subscription.autopay')
case(
    'sbp_recurring_menu',
    ['sbp_recurring_cancel', 'subscription_autopay'],
    'subscription.autopay',
    'subscription.autopay',
)
case('sbp_recurring_enable', ['sbp_recurring_menu'], 'subscription.autopay', 'subscription.autopay')
case('cashera_enable', ['subscription_autopay'], 'subscription.autopay', 'subscription.autopay')
case('saved_cards_list', ['unlink_card_12', 'unlink_card_13', 'menu_balance'], 'balance.cards')
case('unlink_confirm', ['confirm_unlink_12', 'saved_cards_list'], 'balance.cards')
case('cards_empty', ['menu_balance'], 'balance.cards', 'balance.cards')
case('config_back_period', ['period_30', 'period_90', 'back_to_menu'], 'subscription_purchase')
case('config_back_traffic', ['traffic_50', 'traffic_100', 'subscription_config_back'], 'subscription_purchase')
case('config_back_countries', ['country_abc123', 'countries_continue', 'subscription_config_back'], 'subscription')
case(
    'config_back_devices',
    ['devices_3', 'devices_4', 'devices_continue', 'subscription_config_back'],
    'subscription.devices',
)
case('revoke_confirm_classic', ['subscription_revoke_confirm', 'subscription_settings'], 'subscription.revoke')
case('revoke_confirm_multi', ['subscription_revoke_confirm', 'my_subscriptions'], 'subscription.revoke')
case('revoke_success', ['subscription_connect', 'my_subscriptions', 'menu_subscription'], 'subscription')
case('revoke_error', ['menu_subscription'], 'subscription')
case('gift_catalog', ['gift_tariff:2', 'gift_tariff:5', 'gift_my', 'gift_enter_code', 'gift_cancel'], 'subscription')
case('gift_disabled_history', ['gift_my', 'gift_enter_code', 'gift_cancel'], 'subscription')
case('gift_disabled_no_history', ['gift_enter_code', 'gift_cancel'], 'subscription')
case('gift_periods', ['gift_period:2:30', 'gift_period:2:90', 'gift_back_tariffs', 'gift_cancel'], 'subscription')
case('gift_tariff_unavailable', ['gift_back_tariffs'], 'subscription')
case('gift_confirm', ['gift_confirm', 'gift_back_periods', 'gift_cancel'], 'subscription')
case('gift_period_unavailable', ['gift_back_periods'], 'subscription')
case('gift_insufficient', ['topup_amount|stars|7000', 'return_to_gift_cart', 'back_to_menu'], 'balance.topup')
case('gift_success', ['menu_subscription', 'back_to_menu'], 'subscription')
case('gift_no_tariff', ['gift_cancel'], 'subscription')
case('gift_enter_code', ['gift_activation_cancel'], 'subscription')
case('gift_code_errors', ['gift_activation_cancel'], 'subscription')
case('gift_activation_success', ['menu_subscription', 'back_to_menu'], 'subscription')
case('gift_my_empty', ['gift_enter_code', 'gift_back_tariffs'], 'subscription')
case('gift_my_list', ['gift_my_open:12', 'gift_my_page:2', 'gift_back_tariffs'], 'subscription')
case('gift_card_pending', ['gift_my_qr:12', 'gift_my_text:12', 'gift_my_back'], 'subscription')
case('gift_card_activated', ['gift_my_back'], 'subscription')
case('gift_qr_text', ['gift_my_open:12'], 'subscription')
case('gift_activate_branches', [], 'subscription', 'my_subscriptions')
case('countries_manage', ['country_manage_abc123', 'countries_apply', 'sm:9', 'menu_subscription'], 'subscription')
case('countries_insufficient', ['topup_amount|stars|5000', 'back_to_menu'], 'balance.topup')
case('countries_success', ['back_to_menu'], 'subscription', 'my_subscriptions')
case('select_country', ['country_abc123', 'countries_continue', 'subscription_config_back'], 'subscription')
case('countries_devices_step', ['devices_3', 'devices_continue', 'subscription_config_back'], 'subscription.devices')
case('add_countries_insufficient', ['topup_amount|stars|5000', 'back_to_menu'], 'balance.topup')
case('add_countries_success', ['back_to_menu'], 'subscription', 'my_subscriptions')
case('promo_test_servers', ['back_to_menu'], 'promocode', 'promocode')
case('promo_discount_no_sub', ['subscription_upgrade'], 'promocode', 'promocode')
case('promo_discount_sub', ['subscription_extend'], 'promocode', 'promocode')

# ===== balance / payments / simple / stars =====
case('balance_menu', ['balance_history', 'balance_topup', 'saved_cards_list', 'back_to_menu'], 'balance', 'balance')
case(
    'history_multi',
    ['balance_history_page_1', 'current_page', 'balance_history_page_3', 'menu_balance'],
    'balance.history',
)
case('history_one_page', ['menu_balance'], 'balance.history', 'balance.history')
case('history_empty', ['back_to_menu'], 'balance.history', 'balance.history')
case(
    'payment_methods',
    ['topup_stars', 'topup_yookassa_sbp', 'topup_yookassa', 'topup_cryptobot', 'topup_support', 'menu_balance'],
    'balance.topup',
)
case(
    'payment_methods_cart',
    [
        'topup_amount|stars|10000',
        'topup_amount|yookassa|10000',
        'topup_amount|cryptobot|10000',
        'topup_support',
        'menu_balance',
    ],
    'balance.topup',
)
case('payment_methods_unavailable', ['payment_methods_unavailable', 'menu_balance'], 'balance.topup')
case('payment_methods_restricted', ['menu_balance'], 'balance')  # осознанный фолбэк на раздел
case('support_topup', ['balance_topup'], 'balance.topup')
case('topup_success_cart', ['return_to_saved_cart', 'menu_balance', 'back_to_menu'], 'topup_success', 'topup_success')
# Редкие платёжные ошибки ниже: expected='balance' — осознанный фолбэк на раздел
# (экран ошибки показывает баннер раздела баланса, без баннера пополнения).
case('invalid_amount', ['back_to_menu'], 'balance', 'balance')
case('amount_too_small', ['balance_topup'], 'balance.topup')
case('yookassa_limits', ['balance_topup'], 'balance.topup')
case('sbp_instruction', [], 'balance', 'balance')  # осознанный фолбэк на раздел
case('topup_router', [], 'balance', 'balance')  # осознанный фолбэк на раздел
case('stars_amount', ['topup_amount|stars|10000', 'topup_amount|stars|50000', 'back_to_menu'], 'balance.topup')
case('stars_invoice', ['balance_topup'], 'balance.topup')
case('restriction_generic', ['menu_balance'], 'balance')  # осознанный фолбэк на раздел
case(
    'yookassa_card_amount',
    ['topup_amount|yookassa|10000', 'topup_amount|yookassa|50000', 'back_to_menu'],
    'balance.topup',
)
case(
    'yookassa_sbp_amount',
    ['topup_amount|yookassa_sbp|10000', 'topup_amount|yookassa_sbp|50000', 'back_to_menu'],
    'balance.topup',
)
case('yookassa_card_invoice', ['check_yookassa_123', 'balance_topup'], 'balance.topup')
case('yookassa_sbp_invoice', ['temp_disabled', 'check_yookassa_123', 'balance_topup'], 'balance.topup')
case('yookassa_minmax', ['back_to_menu'], 'balance', 'balance')  # осознанный фолбэк на раздел
case('templates_amount', ['topup_amount|riopay|10000', 'topup_amount|riopay|30000', 'menu_balance'], 'balance.topup')
case('templates_invoice', ['menu_balance'], 'balance')  # осознанный фолбэк на раздел
case('templates_minmax', ['back_to_menu'], 'balance', 'balance')  # осознанный фолбэк на раздел
case('crypto_amount', ['topup_amount|cryptobot|10000', 'back_to_menu'], 'balance.topup')
case('crypto_invoice', ['check_cryptobot_45', 'balance_topup'], 'balance.topup')
case('pal24_method', ['pal24_method_sbp', 'pal24_method_card', 'balance_topup'], 'balance.topup')
case('pal24_amount', ['topup_amount|pal24|10000', 'back_to_menu'], 'balance.topup')
case('pal24_invoice', ['check_pal24_45', 'balance_topup'], 'balance.topup')
case('pal24_minmax', ['back_to_menu'], 'balance', 'balance')  # осознанный фолбэк на раздел
case('platega_method', ['platega_method_1', 'platega_method_2', 'platega_method_3', 'balance_topup'], 'balance.topup')
case('platega_amount', ['topup_amount|platega|10000', 'topup_amount|platega|50000', 'back_to_menu'], 'balance.topup')
case('platega_invoice', ['check_platega_45', 'balance_topup'], 'balance.topup')
case('platega_cashera_minmax', ['balance_topup'], 'balance.topup')
case('cashera_method', ['cashera_method_card', 'cashera_method_sbp', 'balance_topup'], 'balance.topup')
case('cashera_amount', ['topup_amount|cashera|10000', 'topup_amount|cashera|50000', 'back_to_menu'], 'balance.topup')
case('cashera_invoice', ['check_cashera_45', 'balance_topup'], 'balance.topup')
case(
    'overpay_methods',
    ['topup_overpay', 'topup_overpay_fps', 'topup_overpay_card', 'topup_overpay_int', 'menu_balance'],
    'balance.topup',
)
case(
    'overpay_amount',
    ['topup_amount|overpay_fps|10000', 'topup_amount|overpay_fps|30000', 'topup_overpay'],
    'balance.topup',
)
case('overpay_invoice', ['menu_balance'], 'balance')  # осознанный фолбэк на раздел
case('overpay_minmax', ['back_to_menu'], 'balance', 'balance')  # осознанный фолбэк на раздел
case('tribute_invoice', ['balance_topup'], 'balance.topup')
case('stars_success_no_sub', ['menu_buy', 'menu_balance', 'back_to_menu'], 'topup_success', 'topup_success')
case('stars_success_sub', ['subscription_extend', 'menu_balance', 'back_to_menu'], 'topup_success', 'topup_success')
case('stars_trial_activated', [], 'trial', 'trial')
case('stars_guest_purchase', [], 'subscription', 'my_subscriptions')
case('stars_wheel', [], None)
case(
    'simple_start',
    [
        'simple_subscription_pay_with_balance',
        'simple_subscription_stars',
        'simple_subscription_yookassa_sbp',
        'simple_subscription_yookassa',
        'simple_subscription_cryptobot',
        'simple_subscription_heleket',
        'simple_subscription_mulenpay',
        'simple_subscription_pal24',
        'simple_subscription_wata',
        'subscription_purchase',
    ],
    'subscription_purchase',
)
case(
    'simple_confirm_active', ['simple_subscription_confirm_purchase', 'subscription_purchase'], 'subscription_purchase'
)
case('simple_restricted', ['subscription'], 'subscription_purchase', 'subscription_purchase')
case(
    'simple_success',
    ['open_subscription_link', 'subscription_happ_download', 'back_to_menu'],
    'subscription_success',
    'subscription_success',
)
case(
    'simple_success_fallback', ['subscription_connect', 'back_to_menu'], 'subscription_success', 'subscription_success'
)
case(
    'simple_other_methods',
    [
        'simple_subscription_pay_with_balance',
        'simple_subscription_stars',
        'simple_subscription_yookassa_sbp',
        'simple_subscription_yookassa',
        'simple_subscription_cryptobot',
        'subscription_purchase',
    ],
    'subscription_purchase',
)
case(
    'simple_yookassa_invoice',
    ['temp_disabled', 'check_yookassa_77', 'subscription_purchase'],
    'subscription_purchase',
    'subscription_purchase',
)
case('simple_crypto_invoice', ['check_simple_cryptobot_77', 'subscription_purchase'], 'subscription_purchase')
case('simple_status', ['check_simple_pal24_77', 'subscription_purchase'], 'subscription_purchase')
case('cards_list_zone5', ['unlink_card_12', 'unlink_card_15', 'menu_balance'], 'balance.cards')
case('unlink_confirm_zone5', ['confirm_unlink_12', 'saved_cards_list'], 'balance.cards')
case('cards_empty_zone5', ['menu_balance'], 'balance.cards', 'balance.cards')

# ===== referral / support / promo / status / start / common =====
case(
    'referral_main',
    [
        'referral_create_invite',
        'referral_show_qr',
        'referral_list',
        'referral_analytics',
        'referral_reward_settings',
        'referral_withdrawal',
        'back_to_menu',
    ],
    'referrals',
    'referrals',
)
case('referral_qr', ['menu_referrals'], 'referrals')
case('referral_list_empty', ['menu_referrals'], 'referral.list', 'referral.list')
case('referral_list_one_page', ['menu_referrals'], 'referral.list', 'referral.list')
case('referral_list_paginated', ['referral_list_page_2', 'referral_list_page_3', 'menu_referrals'], 'referral.list')
case('referral_analytics', ['menu_referrals'], 'referral.analytics', 'referral.analytics')
case('create_invite', ['menu_referrals'], 'referrals')
case(
    'withdrawal_available',
    ['referral_withdrawal_start', 'menu_referrals'],
    'referral.withdrawal',
    'referral.withdrawal',
)
case('withdrawal_unavailable', ['menu_referrals'], 'referral.withdrawal', 'referral.withdrawal')
case('withdrawal_amount', ['referral_withdrawal_amount_15000', 'referral_withdrawal_cancel'], 'referral.withdrawal')
case('withdrawal_errors', [], None)
case('withdrawal_requisites', ['referral_withdrawal_cancel'], 'referral.withdrawal')
case('requisites_short', [], None)
case('withdrawal_confirm', ['referral_withdrawal_confirm', 'referral_withdrawal_cancel'], 'referral.withdrawal')
case('withdrawal_created', ['menu_referrals'], 'referral.withdrawal', 'referral.withdrawal')
case('withdrawal_cancelled', ['menu_referrals'], 'referral.withdrawal', 'referral.withdrawal')
case('support_main', ['create_ticket', 'my_tickets', 'back_to_menu'], 'support', 'support')
case('support_tickets_off', ['back_to_menu'], 'support', 'support')
case('ticket_title', ['cancel_ticket_creation'], 'support')
case('ticket_message', ['cancel_ticket_creation'], 'support')
case('ticket_created', ['view_ticket_5', 'back_to_menu'], 'support')
case('my_tickets_empty', ['create_ticket', 'my_tickets_closed', 'menu_support'], 'support')
case(
    'my_tickets_paginated',
    ['my_tickets_closed', 'view_ticket_1', 'view_ticket_2', 'my_tickets_page_2', 'current_page', 'menu_support'],
    'support',
)
case('closed_empty', ['my_tickets', 'menu_support'], 'support')
case('closed_paginated', ['my_tickets', 'view_ticket_3', 'my_tickets_closed_page_2', 'menu_support'], 'support')
case('view_open', ['reply_ticket_5', 'close_ticket_5', 'my_tickets'], 'support')
case('view_closed', ['my_tickets'], 'support')
case(
    'view_attachments',
    [
        'ticket_attachments_5',
        'reply_ticket_5',
        'close_ticket_5',
        'ticket_view_page_5_1',
        'noop',
        'ticket_view_page_5_2',
        'my_tickets',
    ],
    'support',
)
case('attachments_sent', ['user_delete_message_12345'], 'support')
case('reply_prompt', ['cancel_ticket_reply'], 'support')
case('reply_errors', [], None)
case('reply_success', ['view_ticket_5', 'back_to_menu'], 'support')
case('ticket_closed_notif', ['close_ticket_notification_5'], 'support')
case('close_ticket', ['my_tickets'], 'support')
case('cancel_creation', ['menu_support'], 'support')
case('cancel_reply', ['my_tickets'], 'support')
case('promocode_menu', ['back_to_menu'], 'promocode', 'promocode')
case('promocode_results', ['back_to_menu'], 'promocode', 'promocode')
case('promo_sub_select', ['promo_sub:12:SALE2024', 'promo_sub:15:SALE2024', 'back_to_menu'], 'promocode')
case('promo_sub_result', ['back_to_menu'], 'promocode', 'promocode')
case(
    'server_status',
    ['server_status_page:2', 'server_status_page:1', 'server_status_page:3', 'back_to_menu'],
    'server_status',
    'server_status',
)
case(
    'language_registration',
    ['language_select:ru', 'language_select:en', 'language_select:tr', 'language_select:pl'],
    'language',
)
case('rules_registration', ['rules_accept', 'rules_decline'], 'rules')
case('privacy_registration', ['privacy_policy_accept', 'privacy_policy_decline'], 'privacy_policy')
case('referral_question', ['referral_skip'], 'referrals')
case('reg_errors', [], None)
case('welcome_no_sub', ['trial_activate', 'back_to_menu'], 'trial')
case('welcome_with_sub', ['back_to_menu'], None)
case(
    'start_menu_with_sub',
    [
        'subscription_connect',
        'menu_balance',
        'menu_subscription',
        'menu_promocode',
        'menu_referrals',
        'menu_support',
        'menu_info',
        'menu_language',
    ],
    'main_menu',
    'main_menu',
)
case(
    'start_menu_no_sub',
    ['menu_balance', 'menu_trial', 'menu_buy', 'menu_promocode', 'menu_referrals', 'menu_support', 'menu_info'],
    'main_menu',
    'main_menu',
)
case(
    'start_menu_simple',
    [
        'menu_balance',
        'menu_buy',
        'simple_subscription_purchase',
        'menu_promocode',
        'menu_referrals',
        'menu_support',
        'menu_info',
    ],
    'main_menu',
    'main_menu',
)
case('webauth', ['webauth_confirm:abcdef123456', 'webauth_deny'], None)
case('channel_check', ['sub_channel_check'], None)
case('common_rules', ['back_to_menu'], 'rules', 'rules')
case('cancel_op', ['back_to_menu'], None)
case('unknown_msg', ['back_to_menu'], None)
# Зона «Игры/Конкурсы» (contests.py:91-141), пропущенная аудитом: своего ключа секции
# нет (новый подраздел не заводим — ближайший вариант общий логотип, как у webauth).
case('contests_menu_filled', ['contest_play_clicker_12', 'back_to_menu'], None)
case('contests_menu_empty', ['noop', 'back_to_menu'], None)

fails = []
key_strict = 0
for name, cbs, expected, media in C:
    d = detect(*cbs)
    factual = media if media is not None else d
    if media is None and d == expected:
        key_strict += 1
    if eff(factual) != eff(expected):
        fails.append((name, cbs, expected, media, d))

total = len(C)
print(
    f'Golden banner test: {total} cases, passed {total - len(fails)}/{total} '
    f'(key-strict detect()==expected без media: {key_strict}/{total})'
)
for name, cbs, expected, media, d in fails:
    print(f'FAIL {name}: expected={expected} media={media} detect={d} callbacks={cbs}')
sys.exit(1 if fails else 0)
