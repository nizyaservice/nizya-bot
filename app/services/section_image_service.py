import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import structlog
from aiogram.types import FSInputFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.system_setting import delete_system_setting, get_setting_value, upsert_system_setting
from app.database.models import SystemSetting


logger = structlog.get_logger(__name__)


SECTION_IMAGE_MAIN_MENU = 'main_menu'
SECTION_IMAGE_PROFILE = 'profile'
SECTION_IMAGE_BALANCE = 'balance'
SECTION_IMAGE_TOPUP_SUCCESS = 'topup_success'
SECTION_IMAGE_MY_SUBSCRIPTIONS = 'my_subscriptions'
SECTION_IMAGE_ACTIVE_SUBSCRIPTION = 'active_subscription'
SECTION_IMAGE_PROMOCODE = 'promocode'
SECTION_IMAGE_SUPPORT = 'support'
SECTION_IMAGE_REFERRALS = 'referrals'
SECTION_IMAGE_SUBSCRIPTION_PURCHASE = 'subscription_purchase'
SECTION_IMAGE_SUBSCRIPTION_UPGRADE = 'subscription_upgrade'
SECTION_IMAGE_SUBSCRIPTION_SUCCESS = 'subscription_success'
SECTION_IMAGE_INFO = 'info'
SECTION_IMAGE_FAQ = 'faq'
SECTION_IMAGE_RULES = 'rules'
SECTION_IMAGE_PRIVACY_POLICY = 'privacy_policy'
SECTION_IMAGE_PUBLIC_OFFER = 'public_offer'
SECTION_IMAGE_LANGUAGE = 'language'
SECTION_IMAGE_SERVER_STATUS = 'server_status'
SECTION_IMAGE_TRIAL = 'trial'
SECTION_IMAGE_ADMIN = 'admin'

_SECTION_IMAGE_SETTING_PREFIX = 'ui_section_image.'

# Суффикс en-варианта баннера (русские мелкие подписи для англоязычного
# интерфейса — инверсия языков, осознанный дизайн владельца).
_SECTION_IMAGE_EN_SUFFIX = '.en'


@dataclass(frozen=True)
class SectionImageDefinition:
    key: str
    title_key: str
    title_default: str


SECTION_IMAGE_DEFINITIONS: tuple[SectionImageDefinition, ...] = (
    SectionImageDefinition(
        key=SECTION_IMAGE_MAIN_MENU,
        title_key='ADMIN_SECTION_IMAGE_MAIN_MENU',
        title_default='🏠 Главное меню',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_PROFILE,
        title_key='ADMIN_SECTION_IMAGE_PROFILE',
        title_default='👤 Профиль',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_BALANCE,
        title_key='ADMIN_SECTION_IMAGE_BALANCE',
        title_default='💰 Пополнение баланса',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_TOPUP_SUCCESS,
        title_key='ADMIN_SECTION_IMAGE_TOPUP_SUCCESS',
        title_default='✅ Успешное пополнение',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_MY_SUBSCRIPTIONS,
        title_key='ADMIN_SECTION_IMAGE_MY_SUBSCRIPTIONS',
        title_default='📱 Мои подписки',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_ACTIVE_SUBSCRIPTION,
        title_key='ADMIN_SECTION_IMAGE_ACTIVE_SUBSCRIPTION',
        title_default='✅ Активная подписка',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_PROMOCODE,
        title_key='ADMIN_SECTION_IMAGE_PROMOCODE',
        title_default='🎫 Промокод',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_SUPPORT,
        title_key='ADMIN_SECTION_IMAGE_SUPPORT',
        title_default='🆘 Поддержка',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_REFERRALS,
        title_key='ADMIN_SECTION_IMAGE_REFERRALS',
        title_default='🤝 Партнёрка',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_SUBSCRIPTION_PURCHASE,
        title_key='ADMIN_SECTION_IMAGE_SUBSCRIPTION_PURCHASE',
        title_default='💳 Покупка подписки',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_SUBSCRIPTION_UPGRADE,
        title_key='ADMIN_SECTION_IMAGE_SUBSCRIPTION_UPGRADE',
        title_default='🔄 Продление / апгрейд подписки',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_SUBSCRIPTION_SUCCESS,
        title_key='ADMIN_SECTION_IMAGE_SUBSCRIPTION_SUCCESS',
        title_default='✅ Успешная активация подписки',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_INFO,
        title_key='ADMIN_SECTION_IMAGE_INFO',
        title_default='ℹ️ Информация',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_FAQ,
        title_key='ADMIN_SECTION_IMAGE_FAQ',
        title_default='❓ Частые вопросы',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_RULES,
        title_key='ADMIN_SECTION_IMAGE_RULES',
        title_default='📋 Правила сервиса',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_PRIVACY_POLICY,
        title_key='ADMIN_SECTION_IMAGE_PRIVACY_POLICY',
        title_default='🔒 Политика конфиденциальности',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_PUBLIC_OFFER,
        title_key='ADMIN_SECTION_IMAGE_PUBLIC_OFFER',
        title_default='📄 Публичная оферта',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_LANGUAGE,
        title_key='ADMIN_SECTION_IMAGE_LANGUAGE',
        title_default='🌐 Выбор языка',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_SERVER_STATUS,
        title_key='ADMIN_SECTION_IMAGE_SERVER_STATUS',
        title_default='🌍 Статус серверов',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_TRIAL,
        title_key='ADMIN_SECTION_IMAGE_TRIAL',
        title_default='🎁 Пробный период',
    ),
    SectionImageDefinition(
        key=SECTION_IMAGE_ADMIN,
        title_key='ADMIN_SECTION_IMAGE_ADMIN',
        title_default='⚙️ Админ-панель',
    ),
    # Подпункты разделов (ключи вида '<section>.<sub>' — фолбэк на картинку раздела)
    SectionImageDefinition(
        key='balance.history',
        title_key='ADMIN_SECTION_IMAGE_BALANCE_HISTORY',
        title_default='💰 Баланс → История операций',
    ),
    SectionImageDefinition(
        key='balance.cards',
        title_key='ADMIN_SECTION_IMAGE_BALANCE_CARDS',
        title_default='💰 Баланс → Сохранённые карты',
    ),
    SectionImageDefinition(
        key='balance.topup',
        title_key='ADMIN_SECTION_IMAGE_BALANCE_TOPUP',
        title_default='💰 Баланс → Пополнение',
    ),
    SectionImageDefinition(
        key='subscription.settings',
        title_key='ADMIN_SECTION_IMAGE_SUB_SETTINGS',
        title_default='📱 Подписки → Настройки',
    ),
    SectionImageDefinition(
        key='subscription.devices',
        title_key='ADMIN_SECTION_IMAGE_SUB_DEVICES',
        title_default='📱 Подписки → Устройства',
    ),
    SectionImageDefinition(
        key='subscription.traffic',
        title_key='ADMIN_SECTION_IMAGE_SUB_TRAFFIC',
        title_default='📱 Подписки → Трафик',
    ),
    SectionImageDefinition(
        key='subscription.autopay',
        title_key='ADMIN_SECTION_IMAGE_SUB_AUTOPAY',
        title_default='📱 Подписки → Автопродление',
    ),
    SectionImageDefinition(
        key='subscription.extend',
        title_key='ADMIN_SECTION_IMAGE_SUB_EXTEND',
        title_default='📱 Подписки → Продление',
    ),
    SectionImageDefinition(
        key='subscription.revoke',
        title_key='ADMIN_SECTION_IMAGE_SUB_REVOKE',
        title_default='📱 Подписки → Перевыпуск',
    ),
    SectionImageDefinition(
        key='referral.list',
        title_key='ADMIN_SECTION_IMAGE_REFERRAL_LIST',
        title_default='🤝 Партнёрка → Список рефералов',
    ),
    SectionImageDefinition(
        key='referral.analytics',
        title_key='ADMIN_SECTION_IMAGE_REFERRAL_ANALYTICS',
        title_default='🤝 Партнёрка → Аналитика',
    ),
    SectionImageDefinition(
        key='referral.withdrawal',
        title_key='ADMIN_SECTION_IMAGE_REFERRAL_WITHDRAWAL',
        title_default='🤝 Партнёрка → Вывод средств',
    ),
)

_SECTION_IMAGE_DEFINITIONS_BY_KEY = {definition.key: definition for definition in SECTION_IMAGE_DEFINITIONS}


# Картинки разделов на диске: menu_images/<раздел>/_main.png, <раздел>/<подпункт>.png,
# main_menu.png в корне. en-вариант — тот же путь с суффиксом _en.
#
# Файл используется, когда в system_settings для раздела ничего не загружено:
# так новый набор баннеров виден сразу после деплоя, без ручной заливки каждой
# картинки через админку. Загруженная вручную картинка по-прежнему главнее —
# оператор должен иметь возможность переопределить то, что лежит в репозитории.
BANNERS_DIR = Path(__file__).resolve().parents[2] / 'menu_images'


def get_section_image_path(section_key: str, language: str | None = None) -> Path | None:
    """Путь к файлу баннера раздела; None — файла нет."""
    if not section_key:
        return None
    if '.' in section_key:
        parent, sub = section_key.rsplit('.', 1)
        relative = f'{parent}/{sub}'
    elif section_key == SECTION_IMAGE_MAIN_MENU:
        relative = section_key
    else:
        relative = f'{section_key}/_main'

    candidates = [f'{relative}_en.png', f'{relative}.png'] if language == 'en' else [f'{relative}.png']
    for candidate in candidates:
        path = BANNERS_DIR / candidate
        if path.is_file():
            return path
    return None


# file_id, полученный от Telegram после первой отправки файла, живёт до смены
# файла на диске: иначе каждый показ экрана заново грузил бы в Telegram
# полмегабайта. Отпечаток — mtime, как у логотипа в message_patch.
_FILE_ID_BY_PATH: dict[str, tuple[str, float]] = {}


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def section_file_media(path: Path) -> str | FSInputFile:
    """file_id, если файл уже отправляли и он не менялся, иначе сам файл."""
    cached = _FILE_ID_BY_PATH.get(str(path))
    if cached and cached[1] == _file_mtime(path):
        return cached[0]
    return FSInputFile(path)


def remember_file_photo(media: object, result: object) -> None:
    """Запомнить file_id отправленного файла. Безопасна для любого media."""
    if not isinstance(media, FSInputFile):
        return
    photo = getattr(result, 'photo', None)
    if not photo:
        return
    path = Path(media.path)
    _FILE_ID_BY_PATH[str(path)] = (photo[-1].file_id, _file_mtime(path))


def reset_file_id_cache() -> None:
    """Сбросить кеш file_id (тесты и ручная перезаливка)."""
    _FILE_ID_BY_PATH.clear()


def get_section_image_definition(section_key: str) -> SectionImageDefinition | None:
    return _SECTION_IMAGE_DEFINITIONS_BY_KEY.get(section_key)


def get_section_image_setting_key(section_key: str) -> str:
    return f'{_SECTION_IMAGE_SETTING_PREFIX}{section_key}'


async def get_section_image_file_id(
    db: AsyncSession,
    section_key: str,
    language: str | None = None,
) -> str | None:
    """File_id картинки раздела; при language='en' сначала ключ '<key>.en', затем база.

    Базовый набор баннеров (ru/ua) — английские мелкие подписи; en-набор —
    русские. Обратный фолбэк (база -> .en) не нужен: базовый набор обязателен.
    """
    if language == 'en':
        en_file_id = await get_setting_value(
            db, get_section_image_setting_key(f'{section_key}{_SECTION_IMAGE_EN_SUFFIX}')
        )
        if en_file_id:
            return en_file_id
    if get_section_image_definition(section_key) is None:
        return None
    return await get_setting_value(db, get_section_image_setting_key(section_key))


async def get_section_media_kwargs(
    db: AsyncSession,
    section_key: str,
    language: str | None = None,
) -> dict:
    """Короткий хелпер для хендлеров: media=... если картинка задана, иначе {}.

    language='en' подбирает en-вариант баннера ('<key>.en') с фолбэком на базу;
    None/ru/ua — только базовый вариант.
    """
    tariff_image = get_tariff_image_context()
    if tariff_image:
        return {'media': tariff_image}

    # Баннер — украшение, и сорваться на нём целому экрану нельзя: эта функция
    # стоит в 120+ хендлерах, а исключение из неё уносит весь экран в
    # «Ошибка обработки запроса» (@error_handler). Если настройки прочитать не
    # удалось (нет живой сессии, разрыв соединения с БД), пропускаем залитую в
    # админку картинку и показываем файл из репозитория — он лежит рядом с
    # ботом и БД не требует. В админском экране «Фото разделов» ошибка чтения,
    # наоборот, не глушится: там она и есть предмет разговора.
    try:
        file_id = await get_section_image_file_id(db, section_key, language)
    except Exception as error:
        logger.warning(
            'Не удалось прочитать картинку раздела из настроек, берём файл из репозитория',
            section_key=section_key,
            language=language,
            error=str(error),
        )
        file_id = None
    if file_id:
        return {'media': file_id}

    path = get_section_image_path(section_key, language)
    if path:
        return {'media': section_file_media(path)}
    return {}


# Кеш file_id всех секций для автодетекта (обновляется раз в _CACHE_TTL секунд).
_FILE_ID_CACHE: dict[str, str] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL: float = 60.0


async def get_cached_section_file_id(section_key: str) -> str | None:
    """File_id секции из кеша (массовая загрузка всех ui_section_image.* раз в минуту)."""
    global _CACHE_TS
    now = time.monotonic()
    if now - _CACHE_TS > _CACHE_TTL:
        from app.database.database import AsyncSessionLocal

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(SystemSetting).where(SystemSetting.key.like(f'{_SECTION_IMAGE_SETTING_PREFIX}%'))
                )
                _FILE_ID_CACHE.clear()
                for setting in result.scalars():
                    if setting.value:
                        _FILE_ID_CACHE[setting.key[len(_SECTION_IMAGE_SETTING_PREFIX) :]] = setting.value
        except Exception:
            return _FILE_ID_CACHE.get(section_key)
        _CACHE_TS = now
    return _FILE_ID_CACHE.get(section_key)


# Ключ детекта → фактический ключ раздела в БД (у подписок раздел называется my_subscriptions;
# корень 'referral' сервисных подпунктов referral.* — это раздел 'referrals').
_SECTION_IMAGE_ALIASES = {'subscription': 'my_subscriptions', 'referral': 'referrals'}


async def resolve_cached_section_file_id(section_key: str, language: str | None = None) -> str | None:
    """File_id с фолбэком по иерархии: 'referral.analytics' → 'referral' → алиас → None.

    language='en' сначала пробует кеш-ключ '<key>.en' (фолбэк — обычная иерархия);
    language=None/ru/ua — базовый вариант.
    """
    if language == 'en':
        en_file_id = await get_cached_section_file_id(f'{section_key}{_SECTION_IMAGE_EN_SUFFIX}')
        if en_file_id:
            return en_file_id
    file_id = await get_cached_section_file_id(section_key)
    if file_id:
        return file_id
    if '.' in section_key:
        parent = section_key.rsplit('.', 1)[0]
        file_id = await get_cached_section_file_id(parent)
        if file_id:
            return file_id
        section_key = parent
    alias = _SECTION_IMAGE_ALIASES.get(section_key)
    if alias:
        return await get_cached_section_file_id(alias)
    return None


async def resolve_section_media(section_key: str, language: str | None = None) -> str | FSInputFile | None:
    """Картинка раздела для автодетекта: загруженная в админку, иначе файл из репозитория.

    Иерархия та же, что у resolve_cached_section_file_id: подпункт → раздел →
    алиас. Файл ищется по той же цепочке, иначе подпункт без своей картинки
    остался бы вовсе без баннера, хотя у раздела он есть.
    """
    tariff_image = get_tariff_image_context()
    if tariff_image:
        return tariff_image

    file_id = await resolve_cached_section_file_id(section_key, language)
    if file_id:
        return file_id

    candidates = [section_key]
    if '.' in section_key:
        candidates.append(section_key.rsplit('.', 1)[0])
    alias = _SECTION_IMAGE_ALIASES.get(candidates[-1])
    if alias:
        candidates.append(alias)

    for candidate in candidates:
        path = get_section_image_path(candidate, language)
        if path:
            return section_file_media(path)
    return None


# Картинка выбранного тарифа на время обработки одного апдейта.
#
# Экраны воронки покупки рисуются по-разному: часть зовёт get_section_media_kwargs
# напрямую, часть — обычный message.edit_text, который подбирает баннер
# автодетектом по клавиатуре и о тарифе ничего не знает. Прокинуть тариф
# параметром через весь патч нельзя, поэтому хендлер, загрузивший тариф,
# кладёт его картинку в контекст, а обе ветки её оттуда читают.
#
# ContextVar, а не глобальная переменная: aiogram обрабатывает каждый апдейт в
# своей задаче, и значение не протекает между пользователями.
_TARIFF_IMAGE: ContextVar[str | None] = ContextVar('tariff_image_file_id', default=None)


def set_tariff_image_context(tariff) -> None:
    """Запомнить картинку выбранного тарифа до конца обработки апдейта."""
    _TARIFF_IMAGE.set(getattr(tariff, 'image_file_id', None) or None)


def get_tariff_image_context() -> str | None:
    return _TARIFF_IMAGE.get()


def reset_tariff_image_context() -> None:
    """Сбросить контекст (тесты, а также экраны без выбранного тарифа)."""
    _TARIFF_IMAGE.set(None)


# Кеш языка юзера для автодетекта (telegram_id -> language), TTL 5 минут:
# автодетект в message_patch/photo_message не имеет db_user под рукой.
_USER_LANG_CACHE: dict[int, tuple[float, str | None]] = {}
_USER_LANG_TTL = 300.0


async def get_user_language_cached(telegram_id: int | None) -> str | None:
    """Язык юзера из users по telegram_id (кеш 5 мин). None — не нашли/группа."""
    if not telegram_id:
        return None
    import time as _time

    now = _time.monotonic()
    hit = _USER_LANG_CACHE.get(telegram_id)
    if hit and now - hit[0] < _USER_LANG_TTL:
        return hit[1]
    from sqlalchemy import text as _text

    from app.database.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            row = await db.execute(
                _text('SELECT language FROM users WHERE telegram_id = :tid'),
                {'tid': telegram_id},
            )
            value = row.scalar_one_or_none()
    except Exception:
        return hit[1] if hit else None
    _USER_LANG_CACHE[telegram_id] = (now, value)
    return value


async def invalidate_section_cache() -> None:
    """Сброс кеша (вызывается после загрузки/удаления картинки в админке)."""
    global _CACHE_TS
    _FILE_ID_CACHE.clear()
    _CACHE_TS = 0.0


async def resolve_section_image_file_id(
    db: AsyncSession,
    section_key: str,
    *,
    fallback_keys: tuple[str, ...] = (),
    language: str | None = None,
) -> str | None:
    """File_id с фолбэками; при language='en' каждый ключ сперва пробуется как '<key>.en'."""
    file_id = await get_section_image_file_id(db, section_key, language)
    if file_id:
        return file_id

    for fallback_key in fallback_keys:
        fallback_file_id = await get_section_image_file_id(db, fallback_key, language)
        if fallback_file_id:
            return fallback_file_id

    return None


async def set_section_image_file_id(db: AsyncSession, section_key: str, file_id: str) -> None:
    definition = get_section_image_definition(section_key)
    if definition is None:
        raise ValueError(f'Unknown section image key: {section_key}')

    await upsert_system_setting(
        db,
        get_section_image_setting_key(section_key),
        file_id,
        description=f'File ID for bot UI section image: {definition.title_default}',
    )
    await db.commit()


async def delete_section_image_file_id(db: AsyncSession, section_key: str) -> None:
    if get_section_image_definition(section_key) is None:
        raise ValueError(f'Unknown section image key: {section_key}')

    await delete_system_setting(db, get_section_image_setting_key(section_key))
    await db.commit()
