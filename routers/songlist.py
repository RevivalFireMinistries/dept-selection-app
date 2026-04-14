"""
Song List & Catalog API routes for FirePresenter ↔ Music Team integration.

Endpoints:
  POST /api/songs/sync           — FirePresenter pushes its full song catalog
  GET  /api/songs/catalog        — Returns synced songs for the picker dropdown
  POST /api/songlist/submit      — Music team submits song picks (batch)
  GET  /api/songlist/fetch       — FirePresenter polls for song list (marks fetched)
  GET  /api/songlist/submissions — View submitted song list for a date
  DELETE /api/songlist/{id}      — Remove a submitted song
  POST /api/songs/new            — Music team submits a brand-new song with lyrics
  GET  /api/songs/new            — FirePresenter polls for new songs to import
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel
import json

from database import get_db
from models import SyncedSong, SongListSubmission, NewSongSubmission

router = APIRouter()


# ─── Pydantic schemas ───────────────────────────────────────────────

class SyncSongItem(BaseModel):
    id: int
    title: str
    author: Optional[str] = None
    category: Optional[str] = None

class SongPickItem(BaseModel):
    song_id: int
    song_title: str
    notes: Optional[str] = None
    song_order: int = 0

class SongListSubmitRequest(BaseModel):
    submitter_name: str
    service_date: str  # YYYY-MM-DD
    songs: List[SongPickItem]

class SongSection(BaseModel):
    type: str       # verse, chorus, bridge, etc.
    label: str      # "Verse 1", "Chorus"
    lyrics: str
    orderIndex: int

class NewSongRequest(BaseModel):
    submitter_name: str
    title: str
    author: Optional[str] = None
    category: Optional[str] = "General"
    sections: List[SongSection]


# ─── Song catalog sync (FirePresenter → dptSelectionApp) ────────────

@router.post("/api/songs/sync")
def sync_songs(songs: List[SyncSongItem], db: Session = Depends(get_db)):
    """
    FirePresenter pushes its full song catalog. We upsert by ID and
    skip duplicate titles (case-insensitive, keeping the first occurrence).
    """
    incoming_ids = {s.id for s in songs}

    # Delete songs no longer in FirePresenter
    db.query(SyncedSong).filter(~SyncedSong.id.in_(incoming_ids)).delete(synchronize_session=False)

    # Deduplicate incoming songs by title (case-insensitive), keep first occurrence
    seen_titles = set()
    unique_songs = []
    for s in songs:
        title_key = s.title.strip().lower()
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_songs.append(s)

    # Also check existing DB titles to avoid cross-batch duplicates
    existing_titles = {
        row.title.strip().lower()
        for row in db.query(SyncedSong.title).all()
    }

    added = 0
    updated = 0
    for s in unique_songs:
        existing = db.query(SyncedSong).get(s.id)
        if existing:
            existing.title = s.title
            existing.author = s.author
            existing.category = s.category
            existing.synced_at = datetime.utcnow()
            updated += 1
        else:
            title_key = s.title.strip().lower()
            if title_key not in existing_titles:
                db.add(SyncedSong(
                    id=s.id,
                    title=s.title,
                    author=s.author,
                    category=s.category,
                ))
                existing_titles.add(title_key)
                added += 1

    db.commit()
    return {"status": "ok", "added": added, "updated": updated, "duplicates_skipped": len(songs) - len(unique_songs)}


@router.delete("/api/admin/songs/purge")
def purge_all_songs(db: Session = Depends(get_db)):
    """Admin: delete all synced songs from the catalog"""
    count = db.query(SyncedSong).count()
    db.query(SyncedSong).delete()
    db.commit()
    return {"status": "ok", "deleted": count}


@router.get("/api/admin/songs/stats")
def song_stats(db: Session = Depends(get_db)):
    """Admin: get song catalog stats"""
    total = db.query(SyncedSong).count()
    return {"total": total}


@router.get("/api/songs/catalog")
def get_catalog(db: Session = Depends(get_db)):
    """Returns all synced songs for the music team's picker dropdown."""
    songs = db.query(SyncedSong).order_by(SyncedSong.title).all()
    return [
        {"id": s.id, "title": s.title, "author": s.author, "category": s.category}
        for s in songs
    ]


# ─── Song list submissions (Music Team → FirePresenter) ─────────────

@router.post("/api/songlist/submit")
def submit_song_list(req: SongListSubmitRequest, db: Session = Depends(get_db)):
    """Music team submits their song picks for a service."""
    svc_date = date.fromisoformat(req.service_date)
    added = []
    for song in req.songs:
        sub = SongListSubmission(
            submitter_name=req.submitter_name,
            service_date=svc_date,
            song_id=song.song_id,
            song_title=song.song_title,
            notes=song.notes,
            song_order=song.song_order,
        )
        db.add(sub)
        db.flush()
        added.append(sub.id)

    db.commit()
    return {"status": "ok", "count": len(added), "ids": added}


@router.get("/api/songlist/fetch")
def fetch_song_list(db: Session = Depends(get_db)):
    """
    FirePresenter polls this endpoint. Returns unfetched song list items
    and marks them as fetched.
    """
    items = (
        db.query(SongListSubmission)
        .filter(SongListSubmission.fetched == False)
        .order_by(SongListSubmission.service_date, SongListSubmission.song_order)
        .all()
    )

    result = []
    for item in items:
        result.append({
            "id": item.id,
            "submitter_name": item.submitter_name,
            "service_date": item.service_date.isoformat() if item.service_date else None,
            "song_id": item.song_id,
            "song_title": item.song_title,
            "notes": item.notes,
            "song_order": item.song_order,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        })
        item.fetched = True

    db.commit()
    return {"items": result}


@router.get("/api/songlist/submissions")
def list_submissions(
    service_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """View submitted song list for a specific date. Auto-cleans past submissions."""
    # Auto-clean: delete submissions for dates that have passed
    today = date.today()
    db.query(SongListSubmission).filter(SongListSubmission.service_date < today).delete(
        synchronize_session=False
    )
    db.commit()

    query = db.query(SongListSubmission).order_by(SongListSubmission.song_order)
    if service_date:
        query = query.filter(SongListSubmission.service_date == date.fromisoformat(service_date))
    items = query.all()
    return [
        {
            "id": item.id,
            "submitter_name": item.submitter_name,
            "service_date": item.service_date.isoformat() if item.service_date else None,
            "song_id": item.song_id,
            "song_title": item.song_title,
            "notes": item.notes,
            "song_order": item.song_order,
            "fetched": item.fetched,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in items
    ]


@router.delete("/api/songlist/{item_id}")
def delete_song_pick(item_id: int, db: Session = Depends(get_db)):
    item = db.query(SongListSubmission).get(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


# ─── New song submissions (Music Team → FirePresenter) ──────────────

@router.post("/api/songs/new")
def submit_new_song(req: NewSongRequest, db: Session = Depends(get_db)):
    """Music team submits a brand-new song with full lyrics."""
    sections_data = [s.dict() for s in req.sections]
    sub = NewSongSubmission(
        submitter_name=req.submitter_name,
        title=req.title,
        author=req.author,
        category=req.category or "General",
        sections_json=json.dumps(sections_data),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"status": "ok", "id": sub.id}


@router.get("/api/songs/new")
def fetch_new_songs(db: Session = Depends(get_db)):
    """
    FirePresenter polls this endpoint. Returns unfetched new songs
    and marks them as fetched.
    """
    items = (
        db.query(NewSongSubmission)
        .filter(NewSongSubmission.fetched == False)
        .order_by(NewSongSubmission.created_at)
        .all()
    )

    result = []
    for item in items:
        result.append({
            "id": item.id,
            "submitter_name": item.submitter_name,
            "title": item.title,
            "author": item.author,
            "category": item.category,
            "sections": json.loads(item.sections_json) if item.sections_json else [],
            "created_at": item.created_at.isoformat() if item.created_at else None,
        })
        item.fetched = True

    db.commit()
    return {"items": result}
