# CLAUDE.md — шпаргалка по проекту

Подсказка для будущих сессий: где что лежит и как тут принято работать.
Не пересказ кода — карта. Детальный автоген-индекс всех модулей, классов и
функций: `docs/project_structure_reference.md` (5300+ строк, grep по нему
быстрее, чем чтение исходников).

## Что это

Форк опенсорсного **Bedolaga Bot** v5.0.0 (`BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot`,
MIT) — Telegram-бот для продажи VPN-подписок поверх панели **Remnawave**.
Наш remote: `nizyaservice/nizya-bot`, ветка `main`.

Бренд — **Nizya** (16.10.2026 переименован из ApertuRU). Палитра: основной
`#7B4FC9` (фиолетовый), дополнительный `#9CC94F` (лайм) на глубоком чёрном;
словесный знак «Nizya VPN» — имя основным цветом, «VPN» дополнительным.
Имя ApertuRU осталось только в `.env.aperturu.example` (владелец правит env сам)
и в строке-исключении `!.env.aperturu.example` в `.gitignore`, которая привязана
к имени этого файла. Визуальный мотив — диафрагма (апертура) — сохранён.

Масштаб: ~450k строк Python, 124 таблицы БД, 28 платёжных провайдеров,
638 тест-файлов. Читать весь код бессмысленно — всегда grep по конкретной фиче.

## Стек и запуск

Python 3.14 (весь код async) · aiogram 3 · PostgreSQL 18 + SQLAlchemy 2 + Alembic ·
Redis · FastAPI · structlog · ruff · uv · Docker Compose.

```bash
make up            # docker compose up -d --build (контейнеры: remnawave_bot, _db, _redis)
make down / reload
make test          # uv run pytest -v
make test-all      # + тесты, которым нужен настоящий PostgreSQL (make pg-test-up)
make lint / format / fix     # ruff 0.16.9 (версия пиннится в CI — не расходиться)
make migration m="описание"  # alembic revision --autogenerate
make migrate                 # alembic upgrade head
make docs-structure          # перегенерить docs/project_structure_reference.md
```

Точка входа — `main.py` (одна большая `async def main()` на 1000+ строк:
инициализация БД, фоновые сервисы, веб-сервер, запуск бота). Контейнер:
`CMD ["python", "main.py"]`, healthcheck `GET :8080/health`.

Деплой: push в `main` → `.github/workflows/deploy-vps.yml` (rsync на VPS в
`/opt/nizya-bot/` (SSH-порт из секрета `DEPLOY_PORT`), `docker compose up -d --build`,
ожидание healthy). `.env*` при rsync исключён — прод-конфиг живёт только на VPS.

## Архитектура (app/)

| Каталог | Роль |
|---|---|
| `bot.py` | `setup_bot()` — создаёт Dispatcher, middlewares, регистрирует ~60 роутеров (`X.register_handlers(dp)`). Новый хендлер-модуль нужно добавить сюда |
| `config.py` | `Settings(BaseSettings)` — ~1000 env-переменных + ~400 property-геттеров. 4657 строк. Единственный источник конфига, объект `settings` |
| `handlers/` | aiogram-хендлеры: `start, menu, subscription/, balance/, promocode, referral, support, tickets` + `admin/` (49 модулей админки в Telegram) |
| `services/` | 157 модулей бизнес-логики: `payment/`, `subscription_service`, `monitoring_service`, `broadcast_service`, `system_settings_service` и т.д. |
| `database/` | `models.py` (124 таблицы, 5441 строка) + `crud/` (по модулю на сущность/провайдера) + `database.py` (сессии) |
| `keyboards/` | `inline.py`, `admin.py`, `reply.py` — клавиатуры (callback_data тут же) |
| `external/` | клиенты внешних API: `remnawave_api.py`, платёжные шлюзы, вебхук-парсеры |
| `webserver/` | `unified_app.py` — единый FastAPI на :8080: телеграм-вебхук, платёжные вебхуки, Remnawave-вебхук, статика miniapp/uploads, `/health` |
| `webapi/` | REST API для внешних интеграций (admin API) |
| `cabinet/` | backend веб-кабинета (React-фронт в отдельном репо `bedolaga-cabinet`): auth/OAuth, routes/, RBAC |
| `localization/` | `loader.py` (загрузка `locales/*.json`, flatten ключей) + `texts.py` (`get_texts(lang)` → объект `Texts` с атрибутами по ключам) |
| `middlewares/`, `utils/` | 14 мидлвар; 56 утилит (кэш, форматтеры, telegram-хелперы) |
| `states.py` | все FSM-стейты в одном файле |

Поток запроса: aiogram-хендлер → service → crud → БД; внешние эффекты через
`external/`; тексты через `get_texts(language)`.

### Конфиг: два уровня

`.env` — базовый слой. Поверх него **`system_settings`** (таблица БД):
`app/services/system_settings_service.py` + админка `handlers/admin/bot_configuration.py`
позволяют менять настройки на ходу, и значение из БД перебивает env.
Поэтому «почему не применилось значение из .env» = скорее всего переопределено в БД.

Ключевые переключатели: `SALES_MODE` (`tariffs` по умолчанию / `classic`),
`DEFAULT_LANGUAGE=ru`, `AVAILABLE_LANGUAGES=ru,en,ua,zh,fa`, `BOT_RUN_MODE`
(`polling`/`webhook`), `BOT_TOKEN`, `ADMIN_IDS`, `REMNAWAVE_API_URL`,
`CABINET_URL`, `CABINET_GIFT_ENABLED`.

Шаблоны: `.env.example` (upstream, 81k) и `.env.aperturu.example` (наш брендированный,
набор ключей идентичен — отличаются только значения). При апстрим-мерже сверять оба.

## Наши кастомизации (поверх upstream `12c92f8e`)

Главная добавленная фича — **система баннеров разделов** (картинка-шапка под каждым
экраном бота):

- `app/utils/menu_section_detect.py` — автодетект раздела по callback_data кнопок
  клавиатуры (подпункт специфичнее раздела; первое совпавшее правило выигрывает)
- `app/services/section_image_service.py` — 31 ключ баннера (19 разделов +
  `main_menu` + 11 подпунктов), кэш + фолбэк «подпункт → раздел → `vpn_logo.png`».
  Источники картинки, по убыванию приоритета:
  1. картинка выбранного тарифа (`tariffs.image_file_id`) — через ContextVar
     `set_tariff_image_context()`, который ставят хендлеры воронки покупки;
  2. загруженная в админку: Telegram `file_id` в `system_settings`
     (`ui_section_image.<ключ>`, en-вариант — суффикс `.en`);
  3. файл из `menu_images/` (`<раздел>/_main.png`, `<раздел>/<подпункт>.png`,
     `main_menu.png`, en-вариант — суффикс `_en`). После первой отправки
     `file_id` кэшируется, кэш сбрасывается по mtime файла.
  Поэтому новый набор картинок в `menu_images/` виден сразу после деплоя, а
  заливка через админку перебивает репозиторий
- `app/handlers/admin/section_images.py` — админ → Настройки → «🖼️ Фото разделов»
- `app/utils/photo_message.py` + `app/utils/message_patch.py` — патч методов
  `Message.answer/edit_text`, чтобы любое сообщение уезжало фото+caption
- `menu_images/` — генератор баннеров: `python3 menu_images/generate.py` пишет
  66 SVG и рендерит 67 PNG (нужен пакет `librsvg2-bin` → `rsvg-convert`; шрифт
  DejaVu Bold ищется по списку путей Arch/Debian). Палитра задана константами
  `BRAND` / `BRAND_ALT` / `BRAND_LIGHT` в начале `generate.py` — менять цвета
  только там и перегенерировать. Карта «экран → callback → файл»:
  `menu_images/MENU_MAP.md`, дизайн-правила: `menu_images/README.md`,
  golden-тест: `python3 menu_images/banner_golden_test.py` (311 кейсов, пути
  внутри — относительно репозитория)
- `vpn_logo.png` в корне (`settings.LOGO_FILE`) — общий логотип-фолбэк бота;
  генератор пишет его копию в `menu_images/vpn_logo.png`, после перегенерации
  копировать в корень: `cp menu_images/vpn_logo.png vpn_logo.png`
- `locales/*.json` — наши ключи баннеров/текстов (+~1800 строк в каждую локаль)
- `.github/workflows/deploy-vps.yml` (деплой) и `secret-scan.yml` + `.gitleaks.toml`

Вторая добавленная фича — **роутер-ссылка**: пользователь платит с баланса и
получает `vless://`-ключ одной локации для прошивки в роутер.

- `app/services/router_link_service.py` — настройки (`system_settings`:
  `router_link.price_kopeks`, `router_link.squads`, в `.env` их нет), выборка
  локаций «включённые сквады ∩ сквады пользователя → хосты сквада → ссылка
  подписки по remark», покупка (списание и право одним коммитом)
- `app/handlers/router_link.py` (экраны пользователя, вход — кнопка во вкладке
  «Моя подписка») и `app/handlers/admin/router_link.py` (админка → Настройки →
  «📶 Роутер» → «Стоимость» / «Сквад»)
- модель `RouterLinkPurchase` + миграция `0133`: хранится ПРАВО на локацию, а не
  ключ — ссылка каждый раз берётся из панели
- главное правило: ключ запрашивается у панели только ПОСЛЕ списания, до оплаты
  наружу уходит лишь имя локации. Подробности и причины — `docs/router-link.md`

Третья — **выключатель кнопки «Автопродление через СБП»** (админка → Настройки →
«⚡ Продление через СБП», `app/handlers/admin/sbp_recurring.py` +
`app/services/sbp_recurring_settings.py`). Ключ `sbp_recurring.button_visible` в
`system_settings`, а не поле `Settings`, именно потому, что `PLATEGA_RECURRENT_ENABLED`
задан в прод-`.env`: такие ключи попадают в `ENV_OVERRIDE_KEYS`, и запись из админки
сохранилась бы в БД, но на живой процесс не применилась. Выключатель прячет только
подключение — у кого привязка жива, кнопка остаётся (иначе списания идут, а отменить
нечем; тот же принцип, что у строки Cashera в `handlers/subscription/autopay.py`).

Четвёртая — **кнопки рассылки открывают экран новым сообщением**
(`app/utils/screen_delivery.py` + `app/middlewares/broadcast_buttons.py`).
Кнопки, которые строит `create_broadcast_keyboard`, несут в `callback_data`
метку `bc:`; outer-мидлвара снимает её **до фильтров** (inner не подойдёт:
помеченное нажатие не попало бы ни в один маршрут) и включает ContextVar
`force_new_screen`. Флаг читают две точки отрисовки — `edit_or_answer_photo`
и патченный `Message.edit_text`; хендлеры разделов о нём не знают, поэтому
все прочие флоу по-прежнему правят текущее сообщение.

Пятая — **бонусные баллы**: второй кошелёк рядом с деньгами.
`users.bonus_points_kopeks` (миграция `0134`), 1 балл = 1 ₽ в копейках — чтобы
прораты и проценты считались без пересчёта. Начисляет оператор: админка →
карточка пользователя → «🎁 Баллы» (парная «💰 Баланс»). Тратятся раньше денег,
и вся очерёдность живёт в одной точке — `subtract_user_balance`; отдельным флоу
о баллах знать не нужно. Проверки «хватает ли» в путях оплаты считают по
`User.available_balance_kopeks` (баллы + деньги), а выплаты наружу
(`referral_withdrawal_service`) — по-прежнему только по `balance_kopeks`: баллы
не выводятся. Видны строкой над балансом в обоих главных меню (rich и
классическом), на экране «Баланс» и в `live_menu_fingerprint`, иначе живое меню
не перерисуется после начисления. Наружу (кабинет, мини-апп, портал) баллы
уходят полями `bonus_points_*` и `available_*` в `GET /cabinet/balance`;
портал показывает именно доступную сумму — раздела бонусов там нет, но цифра
обязана совпадать с тем, чем человек может заплатить.

**Правило одной строкой: «хватает ли» считается по `available_balance_kopeks`
везде, где списывает `subtract_user_balance`.** Это и бот, и кабинет, и
мини-апп, и админские покупки подписки пользователю — иначе человеку с баллами
отказывают там, где списание прошло бы. Исключения осознанные: реферальные
выплаты и колесо (только деньги), подписи сумм в списках админки и правка
денежного кошелька оператором — у неё `use_bonus_points=False`, чтобы «снять
100 ₽» не съело баллы. Сторож — `tests/test_bonus_points.py`
(`test_payment_paths_compare_against_both_wallets`); он ловит сравнения и через
промежуточную переменную, и через `getattr` — на обоих видах уже прокалывался.
Начисление баллов оператором оставляет след в журнале админских действий
(`AdminAuditLog`, `action='bonus_points_changed'`): денежная правка пишет
транзакцию, а баллы в историю платежей класть нельзя — исказят отчёты.

Шестая — **регистрация в один шаг**: после `/start` сразу главное меню.
Удалены экраны правил с политикой, вопроса о реферальном коде и выбора языка
(язык нового аккаунта — `DEFAULT_LANGUAGE`, см. `default_registration_language()`
в `handlers/start.py`). Удалены, а не выключены настройками: `SKIP_RULES_ACCEPT`
и `SKIP_REFERRAL_CODE` стояли в прод-`.env` как `false` и перебили бы новое
значение по умолчанию — поэтому сами поля тоже убраны из `Settings` и обоих
`.env*.example`. Вместе с шагами ушли `RegistrationStates`,
`utils/check_reg_process.py`, `utils/long_messages.py`, клавиатуры согласия и
поблажки в мидлварах `auth`/`channel_checker` (незарегистрированного пропускаем
только на `/start` и на проверку подписки на канал). Код из deep link
(`/start ref_…`) по-прежнему работает: его разбирает сама `complete_registration`.
Сторож — `tests/handlers/test_start_has_no_registration_steps.py`.
Что осталось нетронутым: приветственный текст из админки (если задан, после
регистрации показывается он с кнопкой триала, а не меню), проверка подписки на
канал и кнопка «Язык» в меню (прячется настройкой `LANGUAGE_SELECTION_ENABLED`).
Тексты удалённых экранов остались в локалях — на случай возврата флоу.

Остальной fork-diff по хендлерам (`handlers/balance/*`, `subscription/*`, `menu.py`…) —
в основном подстановка `media`/баннеров в вызовы отправки сообщений.

## Веб-портал (`portal/`)

Отдельное приложение рядом с ботом: часть функций бота в браузере. **Не пакет
`app/`** — свой процесс, своя ВПС, своё Redis; в API бота (`app/cabinet/`,
`CABINET_ENABLED=true`) ходит по туннелю WireGuard. В образ бота не попадает
(`portal/` в `.dockerignore`), разворачивается своим compose из
`portal/deploy/`. Карта экранов, причины решений и что нужно поднять —
`portal/deploy/README.md`.

- FastAPI + Jinja (серверный рендер, HTMX по месту), токены человека лежат в
  Redis портала, в браузер уходит только идентификатор сессии (`portal/sessions.py`)
- вход — **только по коду из письма, пароля нет**: `portal/routes/auth.py` →
  ручки бота `/cabinet/auth/email/otp/{request,verify}` (`app/cabinet/auth/login_otp.py`,
  6 цифр, 15 минут, 5 попыток)
- покупка тарифа с баланса: `portal/routes/tariffs.py`. **Цену считает бот** —
  портал показывает его подписи и передаёт выбор; подтверждение GET'ом,
  списание POST'ом с CSRF-токеном
- разделы кабинета: `routes/devices.py` (список, переименование, отключение),
  `promocode.py`, `balance.py` (полный цикл оплаты), `support.py` (тикеты),
  `cabinet.py` (ссылка подписки, документы, язык, рефералка)
- **оплата: деньги зачисляет бот по вебхуку**, портал «успешной оплатой» ничего
  не считает — на возврате спрашивает статус у бота, поэтому заход на страницу
  возврата сам по себе ничего не начисляет. Адрес возврата бот строит из
  `CABINET_URL` (`app/cabinet/routes/balance.py`), поэтому на проде он должен
  указывать на домен портала — см. `portal/deploy/OPERATOR.md`
- реферальная ссылка портала (`/login?ref=КОД`) доезжает до регистрации:
  код едет скрытым полем до `/auth/email/otp/verify`, который его принимает
- шаги запуска для оператора (туннель, SMTP, домен, проверка оплаты) —
  `portal/deploy/OPERATOR.md`
- в `portal/**` нет послаблений ruff (в отличие от `app/**`): локальные импорты,
  неиспользуемые аргументы и прочее ловятся по полной
- тесты — `tests/web_portal/` (каталог назван не `portal`: pytest кладёт
  `tests/` в `sys.path`, и одноимённый пакет перекрыл бы настоящий)
- системы бонусных баллов на портале нет и не планируется — только бот

### Слияние аккаунтов: остаток второй подписки не пропадает

Человек привязывает почту к телеграм-аккаунту (`portal/` ↔ бот), и у него может
оказаться две живых подписки. Правило владельца: **сроки складываются**, а при
разных тарифах остаток пересчитывается по стоимости — сколько дней выбранного
тарифа стоят эти деньги (`PricingEngine.convert_remaining_days`, дневная ставка
та же, что при смене тарифа). Переносит `_absorb_remaining_days` в
`services/account_merge_service.py` — во всех трёх местах, где подписка
перестаёт быть живой (две ветки одиночного режима, конфликт по одному тарифу в
мультитарифе; при разных тарифах мультитариф просто оставляет обе).

Дни добавляются **только** общим CRUD `extend_subscription`: он возвращает
затёртый оверлей грейса и применяет правило трафика тарифа. Присваивать
`end_date` руками нельзя — за это ловят сторожа
`tests/contracts/test_renewal_undoes_grace_echo_first.py` и
`test_renewal_applies_tariff_traffic_rule.py`. Новый срок отправляется в панель
(`_push_end_date_to_panel`), иначе бот считает подписку живой дольше Remnawave.

## Правила работы в репо

- Стиль: ruff с очень широким набором правил (`line-length = 120`, одинарные кавычки,
  `target-version = py313`). Перед коммитом — `make fix`.
- Тесты-сторожа, которые ломаются от «безобидных» правок:
  `tests/test_structure_reference_is_current.py` (после правок структуры —
  `make docs-structure`), `tests/test_locale_integrity.py` (паритет ключей во всех
  5 локалях), `tests/test_readme_payment_providers.py`, `tests/test_sla_defaults_match_env_example.py`,
  `tests/test_grace_env_example_stays_commented.py`, `tests/test_no_undefined_names.py`.
- Новая настройка = поле в `Settings` + строка в **оба** `.env*.example` (+ при
  необходимости определение в `system_settings_service.py`).
- Новый текст для пользователя = ключ во всех `locales/*.json`, обращение через
  `get_texts(language)`; хардкод строк в хендлерах не принят.
- **Локали в два слоя, и правка не в том слое не доезжает до пользователя.**
  `app/localization/locales/*.json` — база (её и проверяет
  `tests/test_locale_integrity.py`), а корневой `locales/*.json` (он же
  `LOCALES_PATH`, в контейнере смонтирован как `/app/locales`) **перебивает**
  её по совпадающим ключам — `loader.py` мержит второй поверх первой. Новый
  ключ достаточно добавить в базу: в корневом слое его нет, фолбэк сработает.
  А вот меняя существующий текст, правь **оба** слоя: иначе на экране
  останется старая строка, причём тесты это поймают не всегда — `load_locale`
  кэшируется, и значение зависит от того, какой тест прогрелся первым.
- Коммиты и комментарии в коде — по-русски (как в истории форка).
- Миграции: только через `make migration`, каталог `migrations/` исключён из ruff.
- `CHANGELOG.md` (500k) — upstream-овский, release-please; руками не трогать.

## Куда смотреть по типовым задачам

| Задача | Файлы |
|---|---|
| Текст/кнопка в меню | `handlers/menu.py`, `keyboards/inline.py`, `locales/ru.json` |
| Новый платёжный провайдер | `external/<gw>*.py` + `services/<gw>_service.py` + `handlers/balance/<gw>.py` + `database/crud/<gw>.py` + модель `<gw>_payments` + вебхук в `webserver/payments.py` |
| Тарифы и цены | `services/tariff_service*`, `handlers/admin/tariffs.py`, `handlers/subscription/tariff_purchase.py`, `utils/pricing_utils.py` |
| Подписки / триал | `services/subscription_service.py`, `handlers/subscription/`, `handlers/admin/trials.py` |
| Интеграция с панелью | `external/remnawave_api.py`, `services/panel_sync/` |
| Баннеры/брендирование | см. раздел «Наши кастомизации» |
| Веб-кабинет | `app/cabinet/` + внешний репо фронта; `docs/web-admin-integration-guide.md` |
| Веб-портал | `portal/` + `portal/deploy/README.md`; тесты `tests/web_portal/` |
| Реферальная/партнёрская | `handlers/referral.py`, `app/referral_levels.py`, `services/partner_*` |

Профильная документация по фичам — в `docs/` (miniapp-setup, broadcast_audiences,
grace-access, websocket-and-webhooks, payments-payer-data и др.), полная
upstream-документация — docs.bedolagam.ru.

## Среда

Рабочая копия `/home/user/vpn/nizya-bot` принадлежит `user` (16.10.2026 сделан
`chown` с root). Системного `pytest`/`uv`/`ruff` в окружении нет — тесты и линт
гоняются через `uv`/Docker; без них доступны `python3 menu_images/banner_golden_test.py`
и проверка синтаксиса. Для генератора баннеров нужен `librsvg2-bin` (установлен).
