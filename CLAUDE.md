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
  `main_menu` + 11 подпунктов), хранение Telegram `file_id` в `system_settings`
  (`ui_section_image.<ключ>`, en-вариант — суффикс `.en`), кэш + фолбэк
  «подпункт → раздел → `vpn_logo.png`»
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

Остальной fork-diff по хендлерам (`handlers/balance/*`, `subscription/*`, `menu.py`…) —
в основном подстановка `media`/баннеров в вызовы отправки сообщений.

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
| Реферальная/партнёрская | `handlers/referral.py`, `app/referral_levels.py`, `services/partner_*` |

Профильная документация по фичам — в `docs/` (miniapp-setup, broadcast_audiences,
grace-access, websocket-and-webhooks, payments-payer-data и др.), полная
upstream-документация — docs.bedolagam.ru.

## Среда

Рабочая копия `/home/user/vpn/nizya-bot` принадлежит `user` (16.10.2026 сделан
`chown` с root). Системного `pytest`/`uv`/`ruff` в окружении нет — тесты и линт
гоняются через `uv`/Docker; без них доступны `python3 menu_images/banner_golden_test.py`
и проверка синтаксиса. Для генератора баннеров нужен `librsvg2-bin` (установлен).
