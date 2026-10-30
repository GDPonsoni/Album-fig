import random
from collections.abc import Sequence
from datetime import date
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PackGrant, PackOpeningItem, PackType, Rarity, Sticker, User, UserSticker
from app.services.core import audit, cfg, lock_user, now

_rng = random.SystemRandom()


def today_local(db: Session) -> date:
    return now().astimezone(ZoneInfo(cfg(db, "daily.timezone"))).date()


def _weight(pack: PackType, rarity: Rarity) -> float:
    return float(pack.rarity_weights.get(str(rarity.id), rarity.weight))


def _pick_sticker(pool: list[Sticker], taken: set[int], rng: random.Random) -> Sticker:
    fresh = [s for s in pool if s.id not in taken]
    return rng.choice(fresh or pool)


def draw_stickers(
    pack: PackType, rarities: Sequence[Rarity], stickers: Sequence[Sticker], rng: random.Random = _rng
) -> list[Sticker]:
    """Sorteia a raridade pelos pesos e depois a figurinha dentro dela.

    Evita repetir figurinha no mesmo pacote enquanto houver opção. Com garantia
    mínima, se nenhuma sorteada atingir o nível, um espaço é trocado por uma
    figurinha sorteada só entre as raridades elegíveis.
    """
    pools: dict[int, list[Sticker]] = {}
    for sticker in stickers:
        if pack.org_filter is None or sticker.org_id == pack.org_filter:
            pools.setdefault(sticker.rarity_id, []).append(sticker)

    drawable = [r for r in rarities if r.id in pools and _weight(pack, r) > 0]
    if not drawable:
        raise HTTPException(409, "Este pacote não tem figurinhas disponíveis no momento.")
    weights = [_weight(pack, r) for r in drawable]

    picked: list[Sticker] = []
    taken: set[int] = set()
    for rarity in rng.choices(drawable, weights=weights, k=pack.stickers_per_pack):
        sticker = _pick_sticker(pools[rarity.id], taken, rng)
        picked.append(sticker)
        taken.add(sticker.id)

    if pack.guaranteed_min_rarity is not None and picked:
        rank_by_id = {r.id: r.rank for r in rarities}
        min_rank = rank_by_id.get(pack.guaranteed_min_rarity)
        if min_rank is not None and not any(rank_by_id[s.rarity_id] >= min_rank for s in picked):
            eligible = [r for r in rarities if r.rank >= min_rank and r.id in pools]
            if eligible:
                # A garantia vale mesmo se o tipo de pacote zerou o peso dessas raridades.
                eligible_weights = [max(_weight(pack, r), 0) for r in eligible]
                if not any(eligible_weights):
                    eligible_weights = [1.0] * len(eligible)
                rarity = rng.choices(eligible, weights=eligible_weights, k=1)[0]
                slot = rng.randrange(len(picked))
                taken.discard(picked[slot].id)
                picked[slot] = _pick_sticker(pools[rarity.id], taken, rng)
    return picked


def grant_pack(db: Session, user_id: int, pack_type_id: int, source: str, source_ref: str | None = None) -> PackGrant:
    grant = PackGrant(user_id=user_id, pack_type_id=pack_type_id, source=source, source_ref=source_ref)
    db.add(grant)
    db.flush()
    audit(db, user_id, "pack.grant", grant_id=grant.id, pack_type_id=pack_type_id, source=source, source_ref=source_ref)
    return grant


def grant_daily_if_due(db: Session, user: User) -> int:
    """Concede os pacotes do dia no primeiro acesso depois da virada. Não acumula
    dias sem acesso. O que passa do teto do estoque diário é descartado."""
    if user.email_verified_at is None:
        return 0
    today = today_local(db)
    if user.last_daily_grant_on == today:
        return 0

    locked = lock_user(db, user.id)
    if locked.last_daily_grant_on == today:
        db.commit()
        return 0

    count: int = cfg(db, "daily.pack_count")
    common = db.scalar(select(PackType).where(PackType.slug == cfg(db, "daily.common_pack_slug")))
    premium = db.scalar(select(PackType).where(PackType.slug == cfg(db, "daily.premium_pack_slug")))
    every: int = cfg(db, "daily.premium_every_n_days")

    # Premium primeiro: se o teto cortar algo, o jogador fica com o pacote melhor.
    plan: list[PackType] = [common] * count
    if premium is not None and every > 0 and count > 0 and today.toordinal() % every == 0:
        plan[0] = premium

    stock = db.scalar(
        select(func.count())
        .select_from(PackGrant)
        .where(PackGrant.user_id == user.id, PackGrant.source == "daily", PackGrant.opened_at.is_(None))
    ) or 0
    room = max(0, int(cfg(db, "daily.stock_cap")) - stock)
    for pack_type in plan[:room]:
        grant_pack(db, user.id, pack_type.id, "daily", today.isoformat())
    discarded = len(plan) - min(room, len(plan))
    if discarded:
        audit(db, user.id, "pack.daily_discarded", count=discarded, day=today.isoformat())

    locked.last_daily_grant_on = today
    db.commit()
    return min(room, len(plan))


def open_pack(db: Session, user: User, grant_id: int) -> tuple[PackGrant, list[tuple[Sticker, bool]]]:
    lock_user(db, user.id)
    grant = db.scalar(
        select(PackGrant).where(PackGrant.id == grant_id, PackGrant.user_id == user.id).with_for_update(of=PackGrant)
    )
    if grant is None:
        raise HTTPException(404, "Pacote não encontrado.")
    if grant.opened_at is not None:
        raise HTTPException(409, "Este pacote já foi aberto.")

    rarities = db.scalars(select(Rarity)).all()
    stickers = db.scalars(select(Sticker).where(Sticker.active.is_(True))).all()
    drawn = draw_stickers(grant.pack_type, rarities, stickers)

    owned = set(
        db.scalars(
            select(UserSticker.sticker_id).where(
                UserSticker.user_id == user.id,
                UserSticker.quantity > 0,
                UserSticker.sticker_id.in_([s.id for s in drawn]),
            )
        )
    )
    result: list[tuple[Sticker, bool]] = []
    for position, sticker in enumerate(drawn):
        is_new = sticker.id not in owned
        owned.add(sticker.id)
        add_to_inventory(db, user.id, sticker.id, 1)
        db.add(PackOpeningItem(pack_grant_id=grant.id, position=position, sticker_id=sticker.id, is_new=is_new))
        result.append((sticker, is_new))

    grant.opened_at = now()
    audit(db, user.id, "pack.open", grant_id=grant.id, stickers=[s.id for s in drawn])
    return grant, result


def add_to_inventory(db: Session, user_id: int, sticker_id: int, quantity: int) -> None:
    stmt = insert(UserSticker).values(user_id=user_id, sticker_id=sticker_id, quantity=quantity)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[UserSticker.user_id, UserSticker.sticker_id],
            set_={"quantity": UserSticker.quantity + stmt.excluded.quantity},
        )
    )
