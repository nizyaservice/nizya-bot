from dataclasses import dataclass
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.system_setting import delete_system_setting, get_setting_value, upsert_system_setting
from app.database.models import SystemSetting


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

_SECTION_IMAGE_SETTING_PREFIX = 'ui_section_image.'


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
)

_SECTION_IMAGE_DEFINITIONS_BY_KEY = {definition.key: definition for definition in SECTION_IMAGE_DEFINITIONS}


def get_section_image_definition(section_key: str) -> SectionImageDefinition | None:
    return _SECTION_IMAGE_DEFINITIONS_BY_KEY.get(section_key)


def get_section_image_setting_key(section_key: str) -> str:
    return f'{_SECTION_IMAGE_SETTING_PREFIX}{section_key}'


async def get_section_image_file_id(db: AsyncSession, section_key: str) -> str | None:
    if get_section_image_definition(section_key) is None:
        return None
    return await get_setting_value(db, get_section_image_setting_key(section_key))


async def get_section_media_kwargs(db: AsyncSession, section_key: str) -> dict:
    """Короткий хелпер для хендлеров: media=... если картинка задана, иначе {}."""
    file_id = await get_section_image_file_id(db, section_key)
    if file_id:
        return {'media': file_id}
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
) -> str | None:
    file_id = await get_section_image_file_id(db, section_key)
    if file_id:
        return file_id

    for fallback_key in fallback_keys:
        fallback_file_id = await get_section_image_file_id(db, fallback_key)
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
