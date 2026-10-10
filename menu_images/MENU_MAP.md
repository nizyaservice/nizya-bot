# Карта меню бота → баннеры

Соответствие «экран бота → callback_data клавиатуры → файл баннера».
Ключ БД = путь файла без `.png`, где `/` → `.` и суффикс `_main`
отбрасывается (`balance/_main.png` → `ui_section_image.balance`,
`referral/analytics.png` → `ui_section_image.referral.analytics`).
Источник правил автодетекта: `app/utils/menu_section_detect.py`
(подпункты проверяются перед разделами; первое совпавшее правило выигрывает;
инфо-комбо — до индивидуальных правил).

| Экран | Callback-паттерны клавиатуры | Баннер (файл) |
|---|---|---|
| Главное меню | `back_to_menu`, `btn_*` | `main_menu.png` (плоский) |
| Профиль | экран профиля (прямая привязка) | `profile/_main.png` |
| Баланс | `balance_topup` | `balance/_main.png` |
|   └ История операций | `balance_history*` | `balance/history.png` |
|   └ Сохранённые карты | `saved_cards*`, `unlink_card_*` | `balance/cards.png` |
|   └ Пополнение | `topup_*`, `check_*`, `*_method`, `payment_methods*`, `yookassa` | `balance/topup.png` |
| Успешное пополнение | экран успеха оплаты (прямая привязка) | `topup_success/_main.png` |
| Подписки | `my_subscriptions`, `subscription`, `subscription_settings`, `subscription_upgrade`, `subscription_autopay`, `sm:`/`se:`/`sl:`/`sd:`/`sr:`/`st:`, `sub_del*`, `country_*`, `custom_confirm:*`, `subscription_*` | `my_subscriptions/_main.png` |
|   └ Устройства | `device_management`, `change_devices*`, `devices_*`, `add_devices_*`, `reset_device_*`, `device_rename`, `reset_all_devices`, `confirm_reset_devices`, `devices_continue` | `subscription/devices.png` |
|   └ Доп. трафик | `buy_traffic`, `add_traffic_*`, `switch_traffic_*`, `confirm_switch_traffic_*`, `confirm_reset_traffic`, `subscription_switch_traffic`, `subscription_reset_traffic` | `subscription/traffic.png` |
|   └ Автопродление | `autopay*` | `subscription/autopay.png` |
|   └ Продление | `subscription_extend`, `extend_period_*` | `subscription/extend.png` |
|   └ Перевыпуск | `subscription_revoke*`, `sr:*`, `sub_del*` | `subscription/revoke.png` |
| Активная подписка | экран активной подписки (прямая привязка) | `active_subscription/_main.png` |
| Покупка подписки | `buy_subscription_tariffs`, `tariff_*`, `custom_days:*`, `custom_traffic:*`, `daily_tariff`, `instant_sw`, `period_*`, `menu_buy` | `subscription_purchase/_main.png` |
| Продление / апгрейд | `subscription_upgrade` (экран апгрейда) | `subscription_upgrade/_main.png` |
| Активация подписки | экран успеха активации (прямая привязка) | `subscription_success/_main.png` |
| Триал | `trial_activate`, `trial_payment*` | `trial/_main.png` |
| Партнёрка | `referral_*` (главный экран партнёрки) | `referrals/_main.png` |
|   └ Список рефералов | `referral_list*` | `referral/list.png` |
|   └ Аналитика | `referral_analytics*` | `referral/analytics.png` |
|   └ Вывод средств | `referral_withdrawal*` | `referral/withdrawal.png` |
| Поддержка | `create_ticket`, `my_tickets`, `view_ticket_*`, `reply_ticket_*`, `close_ticket`, `ticket_attachments_*` | `support/_main.png` |
| Промокод | `promo_sub:*`, `menu_promocode` | `promocode/_main.png` |
| Информация | инфо-комбо: ≥2 из `menu_faq`, `menu_rules`, `menu_privacy_policy`, `menu_public_offer`, `menu_server_status`, `menu_info_promo_groups` | `info/_main.png` |
| FAQ | `menu_faq`, `menu_faq_page:*` | `faq/_main.png` |
| Правила | `menu_rules` | `rules/_main.png` |
| Конфиденциальность | `menu_privacy_policy*` | `privacy_policy/_main.png` |
| Публичная оферта | `menu_public_offer*` | `public_offer/_main.png` |
| Выбор языка | `language_select*` | `language/_main.png` |
| Статус серверов | `server_status_page*` | `server_status/_main.png` |
| Всё нераспознанное | — | `vpn_logo.png` (общий) |

## Примечания

- Администратор может заменить любую картинку через
  **Админ → Настройки → 🖼️ Фото разделов** (бот хранит Telegram `file_id`
  в `system_settings`, ключ `ui_section_image.<ключ>`).
- Иерархия фолбэка при показе: подпункт → раздел → общий логотип
  (`referral.analytics` → `referral` → `vpn_logo.png`).
- «Пополнение» — подпункт баланса (`balance/topup.png`, ключ
  `balance.topup`); прежний отдельный раздел `payment` упразднён — ключ
  `ui_section_image.payment` удаляется из БД.
- Папки `subscription/` и `referral/` подпунктов не имеют своего `_main`:
  главные баннеры этих разделов — `my_subscriptions/_main.png` и
  `referrals/_main.png`.
- Итого ключей в БД: 31 = 19 разделов + `main_menu` + 11 подпунктов.
