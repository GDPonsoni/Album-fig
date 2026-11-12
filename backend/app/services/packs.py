import random
from collections.abc import Sequence
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Album, AlbumParticipation, PackGrant, PackOpeningItem, PackType, Rarity, Sticker, User, UserSticker
from app.services.albums import current_album, get_or_create_participation, participation, require_live, require_same_album
from app.services import provenance
from app.services.core import audit, cfg, lock_user, now

_rng = random.SystemRandom()


def today_local(db: Session, album_id: int | None = None) -> date:
    return now().astimezone(ZoneInfo(cfg(db, "daily.timezone", album_id))).date()


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


def grant_pack(
    db: Session, user_id: int, pack_type: PackType, source: str, source_ref: str | None = None, origin_user_id: int | None = None
) -> PackGrant:
    """O pacote é sempre de um álbum: tipo de pacote modelo (sem álbum) não pode ser concedido."""
    if pack_type.album_id is None:
        raise RuntimeError("Tipo de pacote modelo não pode ser concedido.")
    grant = PackGrant(
        user_id=user_id, album_id=pack_type.album_id, pack_type_id=pack_type.id, source=source, source_ref=source_ref,
        origin_user_id=origin_user_id,
    )
    db.add(grant)
    db.flush()
    audit(
        db, user_id, "pack.grant",
        grant_id=grant.id, album_id=pack_type.album_id, pack_type_id=pack_type.id, source=source, source_ref=source_ref,
        origin_user_id=origin_user_id,
    )
    return grant


def daily_kind(today: date, last_grant_on: date | None, every: int, has_premium: bool) -> str:
    """Regra única de escolha do pacote da gaveta: nos dias de premium, o primeiro
    pacote entregue no dia é o premium. Pura, para a entrega e a previsão não divergirem."""
    if has_premium and every > 0 and today.toordinal() % every == 0 and last_grant_on != today:
        return "premium"
    return "common"


def album_pack_type(db: Session, album_id: int, slug: str) -> PackType | None:
    return db.scalar(select(PackType).where(PackType.album_id == album_id, PackType.slug == slug))


def _daily_pack_types(db: Session, album_id: int) -> dict[str, PackType | None]:
    return {
        "premium": album_pack_type(db, album_id, cfg(db, "daily.premium_pack_slug", album_id)),
        "common": album_pack_type(db, album_id, cfg(db, "daily.common_pack_slug", album_id)),
    }


def _deliver(db: Session, part: AlbumParticipation, today: date) -> None:
    types = _daily_pack_types(db, part.album_id)
    every = int(cfg(db, "daily.premium_every_n_days", part.album_id))
    kind = daily_kind(today, part.last_daily_grant_on, every, types["premium"] is not None)
    grant_pack(db, part.user_id, types[kind], "daily", today.isoformat())
    part.last_daily_grant_on = today


def next_pack_kind(db: Session, part: AlbumParticipation | None) -> dict | None:
    """O que a gaveta entregaria se o jogador abrisse o app no instante `next_pack_at`.
    A entrega usa a data local do momento do acerto: quem só volta em outro dia
    recebe conforme esse outro dia (e, no acúmulo, só o primeiro do dia é premium)."""
    if part is None or part.next_pack_at is None:
        return None
    types = _daily_pack_types(db, part.album_id)
    day = part.next_pack_at.astimezone(ZoneInfo(cfg(db, "daily.timezone", part.album_id))).date()
    every = int(cfg(db, "daily.premium_every_n_days", part.album_id))
    kind = daily_kind(day, part.last_daily_grant_on, every, types["premium"] is not None)
    pack_type = types[kind]
    if pack_type is None:
        return None
    return {"slug": pack_type.slug, "name": pack_type.name, "is_premium": kind == "premium"}


def _settle_drawer(db: Session, part: AlbumParticipation) -> int:
    """Acerta a gaveta de um usuário já travado, no álbum da participação. O timer é
    um ciclo por jogador que só corre enquanto há espaço livre: com a gaveta cheia ele
    para (`next_pack_at` nulo) e recomeça do zero quando um pacote é aberto. O tempo fora
    do app conta. Pacotes de código ficam fora da gaveta."""
    current = now()
    album_id = part.album_id
    today = today_local(db, album_id)
    cap = int(cfg(db, "daily.stock_cap", album_id))
    interval = timedelta(minutes=max(1, int(cfg(db, "daily.refill_minutes", album_id))))
    stock = db.scalar(
        select(func.count())
        .select_from(PackGrant)
        .where(
            PackGrant.user_id == part.user_id,
            PackGrant.album_id == album_id,
            PackGrant.source == "daily",
            PackGrant.opened_at.is_(None),
        )
    ) or 0
    delivered = 0

    if part.last_daily_grant_on is None:
        for _ in range(min(int(cfg(db, "daily.welcome_packs", album_id)), max(0, cap - stock))):
            _deliver(db, part, today)
            delivered += 1
        part.last_daily_grant_on = today

    # Gaveta cheia: o timer fica parado. Só volta a contar quando um espaço é liberado.
    if stock + delivered >= cap:
        part.next_pack_at = None
        return delivered
    if part.next_pack_at is None:
        part.next_pack_at = current + interval
    while stock + delivered < cap and part.next_pack_at <= current:
        _deliver(db, part, today)
        delivered += 1
        part.next_pack_at += interval
    if stock + delivered >= cap:
        part.next_pack_at = None
    return delivered


def refill_drawer(db: Session, user: User) -> int:
    """Acerta a gaveta do álbum em andamento. Sem álbum em andamento, não há gaveta."""
    if user.email_verified_at is None:
        return 0
    album = current_album(db, user)
    if album is None:
        return 0
    part = participation(db, user.id, album.id)
    if part is not None and part.last_daily_grant_on is not None and part.next_pack_at is not None and part.next_pack_at > now():
        return 0  # timer correndo e ainda não venceu
    lock_user(db, user.id)
    delivered = _settle_drawer(db, get_or_create_participation(db, user.id, album.id))
    db.commit()
    return delivered


def open_pack(db: Session, user: User, grant_id: int) -> tuple[PackGrant, list[tuple[Sticker, bool]]]:
    album = require_live(db, user)
    lock_user(db, user.id)
    grant = db.scalar(
        select(PackGrant).where(PackGrant.id == grant_id, PackGrant.user_id == user.id).with_for_update(of=PackGrant)
    )
    if grant is None:
        raise HTTPException(404, "Pacote não encontrado.")
    require_same_album(album, grant.album_id)
    if grant.opened_at is not None:
        raise HTTPException(409, "Este pacote já foi aberto.")

    rarities = db.scalars(select(Rarity)).all()
    # Só figurinhas ativas do álbum do pacote entram no sorteio.
    stickers = db.scalars(select(Sticker).where(Sticker.album_id == album.id, Sticker.active.is_(True))).all()
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
        provenance.record_draw(db, user.id, sticker.id, grant.id, position)
        db.add(PackOpeningItem(pack_grant_id=grant.id, position=position, sticker_id=sticker.id, is_new=is_new))
        result.append((sticker, is_new))

    grant.opened_at = now()
    audit(db, user.id, "pack.open", grant_id=grant.id, album_id=album.id, stickers=[s.id for s in drawn])
    _settle_drawer(db, get_or_create_participation(db, user.id, album.id))
    return grant, result


def add_to_inventory(db: Session, user_id: int, sticker_id: int, quantity: int) -> None:
    stmt = insert(UserSticker).values(user_id=user_id, sticker_id=sticker_id, quantity=quantity)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[UserSticker.user_id, UserSticker.sticker_id],
            set_={"quantity": UserSticker.quantity + stmt.excluded.quantity},
        )
    )


def stock(db: Session, user: User, album: Album) -> list[PackGrant]:
    return list(
        db.scalars(
            select(PackGrant)
            .where(PackGrant.user_id == user.id, PackGrant.album_id == album.id, PackGrant.opened_at.is_(None))
            .order_by(PackGrant.id)
        )
    )


def history(db: Session, user: User, album: Album, limit: int = 20) -> list[tuple[PackGrant, list[tuple[Sticker, bool]]]]:
    """Últimos pacotes abertos do álbum, do mais recente para o mais antigo."""
    grants = db.scalars(
        select(PackGrant)
        .where(PackGrant.user_id == user.id, PackGrant.album_id == album.id, PackGrant.opened_at.is_not(None))
        .order_by(PackGrant.opened_at.desc(), PackGrant.id.desc())
        .limit(limit)
    ).all()
    if not grants:
        return []
    rows = db.execute(
        select(PackOpeningItem.pack_grant_id, Sticker, PackOpeningItem.is_new)
        .join(Sticker, Sticker.id == PackOpeningItem.sticker_id)
        .where(PackOpeningItem.pack_grant_id.in_([g.id for g in grants]))
        .order_by(PackOpeningItem.pack_grant_id, PackOpeningItem.position)
    ).all()
    items: dict[int, list[tuple[Sticker, bool]]] = {}
    for grant_id, sticker, is_new in rows:
        items.setdefault(grant_id, []).append((sticker, is_new))
    return [(grant, items.get(grant.id, [])) for grant in grants]
