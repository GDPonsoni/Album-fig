"""Painel sobre o trabalho dos editores: papéis (só superadmin), fila de revisão e publicação
(superadmin e revisores dos capítulos). A publicação daqui é o único caminho do conteúdo de
um editor para as tabelas de produção."""
from datetime import datetime

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import normalize_email
from app.api.deps import active_grant, promoter_grant, admin_mfa, admin_user, can_review, reviewer_chapter_ids, staff_mfa, staff_user
from app.db import get_db
from app.models import Album, AlbumChapter, AuditLog, ChapterRevision, Rarity, RoleGrant, User
from app.services import revisions
from app.services.albums import album_status, album_view
from app.services.core import audit, enforce_rate, now

router = APIRouter(prefix="/admin", tags=["admin"])


def _chapter(db: Session, chapter_id: int) -> AlbumChapter:
    chapter = db.get(AlbumChapter, chapter_id)
    if chapter is None:
        raise HTTPException(404, "Registro não encontrado.")
    return chapter


def _revision(db: Session, revision_id: int, user: User) -> ChapterRevision:
    """Revisão que `user` pode julgar: de qualquer capítulo para o superadmin, só dos seus para o revisor
    (para os demais é a mesma 404 de revisão inexistente)."""
    revision = db.get(ChapterRevision, revision_id)
    if revision is None or not can_review(db, user, revision.chapter_id):
        raise HTTPException(404, "Registro não encontrado.")
    return revision


# ---- Papéis
class GrantIn(BaseModel):
    email: str = Field(max_length=254)
    expires_at: AwareDatetime | None = None
    role: Literal["editor", "reviewer"] = "editor"


def _grant_view(grant: RoleGrant) -> dict:
    moment = now()
    state = "revoked" if grant.revoked_at else "expired" if grant.expires_at and grant.expires_at <= moment else "active"
    return {
        "id": grant.id, "chapter_id": grant.chapter_id, "user_id": grant.user_id, "nickname": grant.user.nickname,
        "email": grant.user.email, "role": grant.role, "granted_at": grant.granted_at, "expires_at": grant.expires_at,
        "revoked_at": grant.revoked_at, "state": state,
    }


@router.get("/chapters/{chapter_id}/editors")
def list_editors(chapter_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    _chapter(db, chapter_id)
    rows = db.scalars(select(RoleGrant).where(RoleGrant.chapter_id == chapter_id).order_by(RoleGrant.id.desc()))
    return [_grant_view(grant) for grant in rows]


@router.post("/chapters/{chapter_id}/editors", status_code=201)
def grant_editor(chapter_id: int, body: GrantIn, admin: User = Depends(admin_mfa), db: Session = Depends(get_db)):
    chapter = _chapter(db, chapter_id)
    try:
        email = normalize_email(body.email)
    except ValueError as exc:
        raise HTTPException(400, "E-mail inválido.") from exc
    user = db.scalar(select(User).where(User.email == email))
    album = db.get(Album, chapter.album_id)
    if user is None or user.university_id != album.university_id:
        raise HTTPException(404, "Não há conta com este e-mail.")
    if user.email_verified_at is None:
        raise HTTPException(409, "Esta conta ainda não confirmou o e-mail.")
    if body.expires_at is not None and body.expires_at <= now():
        raise HTTPException(400, "A validade precisa estar no futuro.")
    if active_grant(db, user.id, chapter_id, body.role) is not None:
        raise HTTPException(409, "Esta pessoa já tem este papel neste capítulo.")
    grant = RoleGrant(user_id=user.id, chapter_id=chapter_id, role=body.role, granted_by=admin.id, expires_at=body.expires_at)
    db.add(grant)
    db.flush()
    audit(
        db, admin.id, "role.grant",
        grant_id=grant.id, target_user_id=user.id, chapter_id=chapter_id, role=body.role,
        expires_at=body.expires_at.isoformat() if body.expires_at else None,
    )
    db.commit()
    return _grant_view(grant)


@router.delete("/roles/{grant_id}", status_code=204)
def revoke_editor(grant_id: int, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Revogar não pede segundo fator: tirar acesso tem que ser rápido. Vale na requisição seguinte do editor."""
    grant = db.get(RoleGrant, grant_id)
    if grant is None:
        raise HTTPException(404, "Registro não encontrado.")
    if grant.revoked_at is None:
        grant.revoked_at = now()
        audit(db, admin.id, "role.revoke", grant_id=grant.id, target_user_id=grant.user_id, chapter_id=grant.chapter_id)
        db.commit()


# ---- Promotores: papel do álbum todo, sem capítulo
@router.get("/promoters")
def list_promoters(_: User = Depends(admin_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(RoleGrant).where(RoleGrant.role == "promoter").order_by(RoleGrant.id.desc()))
    return [_grant_view(grant) for grant in rows]


@router.post("/promoters", status_code=201)
def grant_promoter(body: GrantIn, admin: User = Depends(admin_mfa), db: Session = Depends(get_db)):
    try:
        email = normalize_email(body.email)
    except ValueError as exc:
        raise HTTPException(400, "E-mail inválido.") from exc
    user = db.scalar(select(User).where(User.email == email))
    if user is None or user.university_id != admin.university_id:
        raise HTTPException(404, "Não há conta com este e-mail.")
    if user.email_verified_at is None:
        raise HTTPException(409, "Esta conta ainda não confirmou o e-mail.")
    if body.expires_at is not None and body.expires_at <= now():
        raise HTTPException(400, "A validade precisa estar no futuro.")
    if promoter_grant(db, user.id) is not None:
        raise HTTPException(409, "Esta pessoa já é promotora.")
    grant = RoleGrant(user_id=user.id, chapter_id=None, role="promoter", granted_by=admin.id, expires_at=body.expires_at)
    db.add(grant)
    db.flush()
    audit(db, admin.id, "role.grant", grant_id=grant.id, target_user_id=user.id, role="promoter",
          expires_at=body.expires_at.isoformat() if body.expires_at else None)
    db.commit()
    return _grant_view(grant)


# ---- Revisões
class NoteIn(BaseModel):
    note: str = Field(default="", max_length=2000)


class StickerOverride(BaseModel):
    rarity_id: int | None = None
    number: int | None = Field(default=None, ge=1, le=100000)


class PublishIn(BaseModel):
    """O superadmin decide número e raridade na publicação; o que não vier usa o padrão
    (próximo número livre; raridade sugerida pelo editor ou a mais comum)."""

    stickers: dict[str, StickerOverride] = Field(default_factory=dict)
    mosaic_rarity_id: int | None = None


def _summary(db: Session, revision: ChapterRevision) -> dict:
    chapter = _chapter(db, revision.chapter_id)
    album = db.get(Album, chapter.album_id)
    author = db.get(User, revision.author_id) if revision.author_id else None
    return {
        "id": revision.id,
        "number": revision.number,
        "status": revision.status,
        "chapter_id": chapter.id,
        "org_name": chapter.org.name,
        "org_color": chapter.org.theme_color,
        "album": album_view(album),
        "author": author.nickname if author else None,
        "contributors": [u.nickname for u in (db.get(User, uid) for uid in revision.contributors or []) if u],
        "updated_at": revision.updated_at,
        "submitted_at": revision.submitted_at,
        "reviewed_at": revision.reviewed_at,
        "published_at": revision.published_at,
        "review_note": revision.review_note,
        "is_live": chapter.published_revision_id == revision.id,
        "can_rollback": chapter.published_revision_id == revision.id and bool(revision.previous_content),
    }


@router.get("/revisions")
def list_revisions(
    status: str = "submitted", chapter_id: int | None = None, user: User = Depends(staff_user), db: Session = Depends(get_db)
):
    """Fila de revisão: por padrão as enviadas, da mais antiga para a mais nova. Revisor só vê os seus capítulos."""
    query = select(ChapterRevision)
    if not user.is_admin:
        mine = reviewer_chapter_ids(db, user)
        if chapter_id is not None and chapter_id not in mine:
            raise HTTPException(404, "Registro não encontrado.")
        query = query.where(ChapterRevision.chapter_id.in_(mine))
    if chapter_id is not None:
        query = query.where(ChapterRevision.chapter_id == chapter_id).order_by(ChapterRevision.number.desc())
    else:
        if status not in ("draft", "submitted", "approved", "rejected", "published", "superseded"):
            raise HTTPException(400, "Estado desconhecido.")
        query = query.where(ChapterRevision.status == status).order_by(ChapterRevision.submitted_at, ChapterRevision.id)
    return [_summary(db, revision) for revision in db.scalars(query.limit(200))]


@router.get("/revisions/{revision_id}")
def revision_detail(revision_id: int, user: User = Depends(staff_user), db: Session = Depends(get_db)):
    revision = _revision(db, revision_id, user)
    chapter = _chapter(db, revision.chapter_id)
    album = db.get(Album, chapter.album_id)
    published = revisions.production_content(db, chapter)
    result = {
        **_summary(db, revision),
        "content": revision.content,
        "published": published["content"],
        "published_meta": published["meta"],
        "diff": revisions.diff(db, chapter, revision.content),
        "structural": revisions.structural_problems(db, chapter, revisions.parse_content(revision.content)),
        "album_status": album_status(album),
        "rarities": [
            {"id": r.id, "name": r.name, "rank": r.rank, "color": r.color} for r in db.scalars(select(Rarity).order_by(Rarity.rank))
        ],
        "org": {
            "id": chapter.org.id, "name": chapter.org.name, "description": chapter.org.description,
            "logo_url": chapter.org.logo_url, "color": chapter.org.theme_color,
        },
        "assets": [
            {"id": a.id, "url": a.url, "kind": a.kind, "status": a.status}
            for a in revisions.chapter_assets(db, chapter.id).values()
        ],
    }
    db.commit()
    return result


@router.post("/revisions/{revision_id}/approve")
def approve(revision_id: int, body: NoteIn, admin: User = Depends(staff_mfa), db: Session = Depends(get_db)):
    revision = revisions.review(db, admin, _revision(db, revision_id, admin), True, body.note)
    db.commit()
    return _summary(db, revision)


@router.post("/revisions/{revision_id}/reject")
def reject(revision_id: int, body: NoteIn, admin: User = Depends(staff_user), db: Session = Depends(get_db)):
    revision = revisions.review(db, admin, _revision(db, revision_id, admin), False, body.note)
    db.commit()
    return _summary(db, revision)


@router.post("/revisions/{revision_id}/publish")
def publish(revision_id: int, body: PublishIn, admin: User = Depends(staff_mfa), db: Session = Depends(get_db)):
    enforce_rate(db, f"publish:{admin.id}", "rate.publish_per_hour")
    db.commit()
    overrides = {key: value.model_dump(exclude_none=True) for key, value in body.stickers.items()}
    revision = revisions.publish(db, admin, _revision(db, revision_id, admin), overrides, body.mosaic_rarity_id)
    db.commit()
    return _summary(db, revision)


@router.post("/revisions/{revision_id}/rollback")
def rollback(revision_id: int, admin: User = Depends(staff_mfa), db: Session = Depends(get_db)):
    enforce_rate(db, f"publish:{admin.id}", "rate.publish_per_hour")
    db.commit()
    restored = revisions.rollback(db, admin, _revision(db, revision_id, admin))
    db.commit()
    return _summary(db, restored)


@router.get("/chapters/{chapter_id}/audit")
def chapter_audit(chapter_id: int, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    """Tudo o que foi auditado sobre o capítulo: papéis, envios, aprovações, publicações, rollbacks."""
    _chapter(db, chapter_id)
    rows = db.execute(
        select(AuditLog, User.nickname)
        .outerjoin(User, User.id == AuditLog.user_id)
        .where(AuditLog.payload["chapter_id"].as_integer() == chapter_id)
        .order_by(AuditLog.id.desc())
        .limit(200)
    ).all()
    return [{"id": log.id, "action": log.action, "by": nickname, "at": log.created_at, "payload": log.payload} for log, nickname in rows]


@router.get("/editors/overview")
def overview(album_id: int | None = None, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    """O que cada org está fazendo: editores ativos, estado da revisão em aberto e a última edição."""
    query = select(AlbumChapter).join(Album, Album.id == AlbumChapter.album_id).where(Album.university_id == admin.university_id)
    if album_id is not None:
        query = query.where(AlbumChapter.album_id == album_id)
    result = []
    moment: datetime = now()
    for chapter in db.scalars(query.order_by(AlbumChapter.album_id.desc(), AlbumChapter.sort_order, AlbumChapter.id)).unique():
        revision = revisions.open_revision(db, chapter.id)
        last = db.scalar(select(func.max(ChapterRevision.updated_at)).where(ChapterRevision.chapter_id == chapter.id))
        grants = db.scalars(
            select(RoleGrant).where(
                RoleGrant.chapter_id == chapter.id, RoleGrant.revoked_at.is_(None),
                (RoleGrant.expires_at.is_(None)) | (RoleGrant.expires_at > moment),
            )
        ).all()
        people = lambda role: [  # noqa: E731
            {"grant_id": g.id, "nickname": g.user.nickname, "email": g.user.email, "expires_at": g.expires_at}
            for g in grants if g.role == role
        ]
        album = db.get(Album, chapter.album_id)
        result.append(
            {
                "chapter_id": chapter.id, "album_id": album.id, "album_title": album.title, "album_status": album_status(album),
                "org_name": chapter.org.name, "org_color": chapter.org.theme_color,
                "editors": people("editor"),
                "reviewers": people("reviewer"),
                "revision": {"id": revision.id, "number": revision.number, "status": revision.status} if revision else None,
                "last_edit_at": last,
                "published_revision_id": chapter.published_revision_id,
            }
        )
    return result
