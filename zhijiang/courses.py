"""Saved course outputs and community publications, independent of generation jobs."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import shutil
import sqlite3
import tempfile
import unicodedata
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from zhijiang.models import JobStatus, Lesson
from zhijiang.storage import JobStore, utc_now


BROWSER_COOKIE = "livelyclass-browser"
SESSION_COOKIE = "livelyclass-session"
SESSION_SECONDS = 30 * 24 * 60 * 60
BROWSER_SECONDS = 365 * 24 * 60 * 60
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
IDENTIFIER = re.compile(r"[a-f0-9]{32}")
PRIVATE_CACHE = {"Cache-Control": "private, no-store"}
ASSETS = {
    "video": ("lesson.mp4", "video/mp4"),
    "presentation": ("lesson.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "cover": ("cover.png", "image/png"),
}


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _cookie_digest(request: Request, name: str) -> bytes | None:
    token = request.cookies.get(name, "")
    return _digest(token) if TOKEN.fullmatch(token) else None


def _set_cookie(request: Request, response: Response, name: str, token: str, max_age: int) -> None:
    response.set_cookie(name, token, max_age=max_age, httponly=True,
                        secure=request.url.scheme == "https", samesite="lax")


def ensure_browser(request: Request, response: Response, *, refresh: bool = False) -> bytes:
    token = request.cookies.get(BROWSER_COOKIE, "")
    if not TOKEN.fullmatch(token):
        token = secrets.token_urlsafe(32)
        refresh = True
    if refresh:
        _set_cookie(request, response, BROWSER_COOKIE, token, BROWSER_SECONDS)
    return _digest(token)


def _json_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _seconds(value: object) -> float | None:
    if type(value) in (float, int) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _timed_cues(records: object, original: str) -> list[dict] | None:
    if not isinstance(records, list) or not records:
        return None
    cues = []
    previous_end = 0.0
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("text"), str):
            return None
        start, end = _seconds(record.get("start")), _seconds(record.get("end"))
        if start is None or end is None or start < previous_end - .005 or end <= start:
            return None
        cues.append({"text": record["text"], "start_seconds": start, "end_seconds": end})
        previous_end = end
    if re.sub(r"\s+", "", "".join(cue["text"] for cue in cues)) != re.sub(r"\s+", "", original):
        return None
    return cues


def _srt_cues(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return []

    def seconds(match, index):
        hours, minutes, whole_seconds, ms = map(int, match.group(index).replace(",", ":").split(":"))
        return hours * 3600 + minutes * 60 + whole_seconds + ms / 1000

    records = []
    stamp = r"(\d{2}:\d{2}:\d{2},\d{3})"
    for block in re.split(r"\n[ \t]*\n", text.strip()):
        match = re.match(rf"(?:\d+\n)?{stamp}\s*-->\s*{stamp}[^\n]*\n(.+)", block, re.S)
        if not match:
            return []
        records.append({"text": match.group(3), "start": seconds(match, 1), "end": seconds(match, 2)})
    return records


def _transcript(lesson: Lesson, folder: Path) -> list[dict]:
    narrations = [segment.narration for segment in lesson.segments]
    original = "".join(narrations)
    subtitles = _json_file(folder / "subtitles.json").get("cues")
    cues = _timed_cues(subtitles, original)
    if cues is None and (folder / "subtitles.srt").is_file():
        cues = _timed_cues(_srt_cues(folder / "subtitles.srt"), original)
    if cues is not None:
        return cues

    timeline = _json_file(folder / "video-timeline.json")
    records = timeline.get("segments", [])
    unaligned = [{"text": text, "start_seconds": None, "end_seconds": None} for text in narrations]
    if not isinstance(records, list) or len(records) != len(narrations):
        return unaligned
    bookends = timeline.get("bookends", [])
    if not isinstance(bookends, list):
        return unaligned
    covers = [_seconds(item.get("duration")) for item in bookends
              if isinstance(item, dict) and item.get("kind") == "cover"]
    if any(seconds is None for seconds in covers):
        return unaligned
    cursor = sum(covers)
    cues = []
    for index, (record, text) in enumerate(zip(records, narrations), start=1):
        duration = _seconds(record.get("duration")) if isinstance(record, dict) else None
        if not duration or record.get("segment_id") != index:
            return unaligned
        cues.append({"text": text, "start_seconds": cursor, "end_seconds": cursor + duration})
        cursor += duration
    return cues


def _learning_plan(lesson: Lesson, folder: Path) -> dict | None:
    plan = lesson.deck_plan.get("learning_plan")
    if not isinstance(plan, dict):
        plan = _json_file(folder / "learning-plan.json")
    sessions = plan.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        return None
    fields = ("day", "title", "minutes", "activities", "self_check", "segment_ids", "source_pages")
    profile = plan.get("profile")
    # The published plan contains study material. Learner inputs and source
    # excerpts from the planning catalogue are not part of that material.
    return {
        "overview": plan.get("overview", ""),
        "profile": {"minutes_per_day": profile.get("minutes_per_day")} if isinstance(profile, dict) else {},
        "total_minutes": plan.get("total_minutes"),
        "time_note": plan.get("time_note", ""),
        "sessions": [{key: session[key] for key in fields if key in session}
                     for session in sessions if isinstance(session, dict)],
    }


class CourseLibrary:
    def __init__(self, jobs: JobStore):
        self.jobs = jobs
        self.directory = jobs.data_dir / "courses"
        self.directory.mkdir(exist_ok=True)
        self.db_path = jobs.data_dir / "courses.sqlite3"
        self.archive_lock = Lock()
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS courses (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    has_cover INTEGER NOT NULL,
                    content_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saves (
                    browser BLOB NOT NULL,
                    course_id TEXT NOT NULL REFERENCES courses(id),
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (browser, course_id)
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL,
                    password BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_saves (
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    course_id TEXT NOT NULL REFERENCES courses(id),
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, course_id)
                );
                CREATE INDEX IF NOT EXISTS account_saves_course ON account_saves(course_id);
                CREATE TABLE IF NOT EXISTS sessions (
                    token BLOB PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publications (
                    course_id TEXT PRIMARY KEY REFERENCES courses(id),
                    author_id INTEGER NOT NULL REFERENCES users(id),
                    published_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publication_views (
                    course_id TEXT NOT NULL REFERENCES publications(course_id),
                    playback_id TEXT NOT NULL,
                    PRIMARY KEY (course_id, playback_id)
                ) WITHOUT ROWID;
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _job_archive(db: sqlite3.Connection, job_id: str, user_id: int | None) -> sqlite3.Row | None:
        return db.execute("""
            SELECT c.id, p.author_id,
                   EXISTS(SELECT 1 FROM account_saves s WHERE s.course_id=c.id AND s.user_id=?) AS saved,
                   EXISTS(SELECT 1 FROM account_saves s WHERE s.course_id=c.id) AS account_owned
            FROM courses c LEFT JOIN publications p ON p.course_id=c.id
            WHERE c.job_id=?
        """, (user_id, job_id)).fetchone()

    def can_access_job(self, job_id: str, user_id: int | None) -> bool:
        with closing(self._connect()) as db:
            archive = self._job_archive(db, job_id, user_id)
        if archive is None or (not archive["account_owned"] and archive["author_id"] is None):
            return True
        return user_id is not None and (bool(archive["saved"]) or archive["author_id"] == user_id)

    def _archive(self, db: sqlite3.Connection, job_id: str, browser: bytes | None,
                 user_id: int, *, publish: bool = False) -> str:
        unavailable = HTTPException(404, "任务不存在。", headers=PRIVATE_CACHE)
        if not IDENTIFIER.fullmatch(job_id):
            raise unavailable
        existing = self._job_archive(db, job_id, user_id)
        if existing:
            author = existing["author_id"]
            if publish and author is not None and author != user_id:
                raise unavailable
            if existing["saved"] or author == user_id:
                return existing["id"]
            if existing["account_owned"] or author is not None:
                raise unavailable
        job = self.jobs.get(job_id)
        # Legacy CLI jobs retain job-ID access. Web drafts use the browser that created them.
        source_browser = self.jobs.browser_owner(job_id) if job else None
        if not job or (source_browser is not None and source_browser != browser):
            raise unavailable
        if job["status"] != JobStatus.COMPLETED:
            raise HTTPException(409, "课程完成后可以保存或分享。", headers=PRIVATE_CACHE)
        if existing:
            return existing["id"]
        lesson = self.jobs.lesson(job_id)
        folder = self.jobs.jobs_dir / job_id
        media = [folder / ASSETS[kind][0] for kind in ("video", "presentation")]
        if not lesson or not all(path.is_file() for path in media):
            raise HTTPException(409, "课程成品不完整。", headers=PRIVATE_CACHE)
        cover = folder / "presentation-preview" / "page-001.png"
        has_cover = cover.is_file()
        course_id = uuid.uuid4().hex
        destination = self.directory / course_id
        content = {"transcript": _transcript(lesson, folder), "learning_plan": _learning_plan(lesson, folder)}
        with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=self.directory) as temporary:
            snapshot = Path(temporary) / "ready"
            snapshot.mkdir()
            for path in media:
                shutil.copyfile(path, snapshot / path.name)
            if has_cover:
                shutil.copyfile(cover, snapshot / ASSETS["cover"][0])
            snapshot.rename(destination)
            try:
                with db:
                    db.execute("INSERT INTO courses VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (course_id, job_id, lesson.title, lesson.objective, utc_now(), has_cover,
                                json.dumps(content, ensure_ascii=False, allow_nan=False)))
            except Exception:
                shutil.rmtree(destination)
                raise
        return course_id

    def save(self, job_id: str, browser: bytes | None, user_id: int) -> dict:
        # Serialize authorization and ownership writes with source-job deletion and legacy imports.
        with self.archive_lock, closing(self._connect()) as db, db:
            course_id = self._archive(db, job_id, browser, user_id)
            db.execute("INSERT OR IGNORE INTO account_saves VALUES (?, ?, ?)", (user_id, course_id, utc_now()))
        return self.get(course_id, user_id=user_id)

    def publish(self, job_id: str, browser: bytes | None, author: int) -> dict:
        with self.archive_lock, closing(self._connect()) as db, db:
            course_id = self._archive(db, job_id, browser, author, publish=True)
            db.execute("INSERT OR IGNORE INTO publications VALUES (?, ?, ?)", (course_id, author, utc_now()))
        return self.get(course_id, public=True)

    def legacy_count(self, browser: bytes | None) -> int:
        if browser is None:
            return 0
        with closing(self._connect()) as db:
            return db.execute("SELECT COUNT(*) FROM saves WHERE browser=?", (browser,)).fetchone()[0]

    def import_browser(self, browser: bytes | None, user_id: int) -> list[str]:
        if browser is None:
            return []
        with self.archive_lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            courses = db.execute("SELECT course_id FROM saves WHERE browser=? ORDER BY saved_at DESC, course_id",
                                 (browser,)).fetchall()
            db.execute("""
                INSERT OR IGNORE INTO account_saves (user_id, course_id, saved_at)
                SELECT ?, course_id, saved_at FROM saves WHERE browser=?
            """, (user_id, browser))
            db.execute("DELETE FROM saves WHERE browser=?", (browser,))
        return [row["course_id"] for row in courses]

    def record_view(self, course_id: str, playback_id: str) -> int:
        if not IDENTIFIER.fullmatch(course_id):
            raise HTTPException(404, "课程不存在。")
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO publication_views "
                "SELECT course_id, ? FROM publications WHERE course_id=?",
                (playback_id, course_id),
            )
            row = db.execute(
                "SELECT COUNT(v.playback_id) FROM publications p "
                "LEFT JOIN publication_views v ON v.course_id=p.course_id "
                "WHERE p.course_id=? GROUP BY p.course_id",
                (course_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "课程不存在。")
            return row[0]

    @staticmethod
    def _representation(row: sqlite3.Row, public: bool) -> dict:
        route = "community/courses" if public else "courses"
        prefix = f"/api/{route}/{row['id']}/assets"
        summary = {
            "id": row["id"],
            "title": row["title"],
            "objective": row["objective"],
            "created_at": row["created_at"],
            "published_at": row["published_at"],
            "author": row["author"],
            "assets": {"video": f"{prefix}/video", "presentation": f"{prefix}/presentation",
                       "cover": f"{prefix}/cover" if row["has_cover"] else None},
        }
        if public:
            summary["view_count"] = row["view_count"]
        return summary

    def list(self, *, user_id: int | None = None, public: bool = False) -> list[dict]:
        return [self._representation(row, public) for row in self._read(user_id=user_id, public=public)]

    def get(self, course_id: str, *, user_id: int | None = None, public: bool = False,
            detail: bool = False) -> dict | None:
        if not IDENTIFIER.fullmatch(course_id):
            return None
        rows = self._read(user_id=user_id, public=public, course_id=course_id, detail=detail)
        if not rows:
            return None
        data = self._representation(rows[0], public)
        if detail:
            data.update(json.loads(rows[0]["content_json"]))
        return data

    def _read(self, *, user_id: int | None, public: bool, course_id: str | None = None,
              detail: bool = False) -> list[sqlite3.Row]:
        if not public and user_id is None:
            return []
        query = """SELECT c.id, c.title, c.objective, c.created_at, c.has_cover,
                          p.published_at, u.username AS author"""
        if public:
            query += ", (SELECT COUNT(*) FROM publication_views v WHERE v.course_id=c.id) AS view_count"
        if detail:
            query += ", c.content_json"
        query += " FROM courses c"
        if public:
            query += " JOIN publications p ON p.course_id=c.id"
        else:
            query += " JOIN account_saves s ON s.course_id=c.id LEFT JOIN publications p ON p.course_id=c.id"
        query += " LEFT JOIN users u ON u.id=p.author_id WHERE 1=1"
        parameters = []
        if not public:
            query += " AND s.user_id=?"
            parameters.append(user_id)
        if course_id:
            query += " AND c.id=?"
            parameters.append(course_id)
        query += " ORDER BY p.published_at DESC, c.id" if public else " ORDER BY s.saved_at DESC, c.id"
        with closing(self._connect()) as db:
            return db.execute(query, parameters).fetchall()

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024**2)

    def register(self, username: str, password: str) -> sqlite3.Row:
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt)
        with closing(self._connect()) as db, db:
            try:
                user = db.execute(
                    "INSERT INTO users (username, username_key, salt, password) VALUES (?, ?, ?, ?) RETURNING id, username",
                    (username, username.casefold(), salt, password_hash),
                ).fetchone()
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409, "该用户名已被使用。") from exc
        return user

    def authenticate(self, username: str, password: str) -> sqlite3.Row:
        with closing(self._connect()) as db:
            user = db.execute("SELECT * FROM users WHERE username_key=?", (username.casefold(),)).fetchone()
        supplied = self._password_hash(password, user["salt"] if user else bytes(16))
        if not hmac.compare_digest(supplied, user["password"] if user else bytes(64)) or user is None:
            raise HTTPException(401, "用户名或密码错误。")
        return user

    def start_session(self, user_id: int, previous: bytes | None) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=SESSION_SECONDS)
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE expires_at<=? OR token=?", (now.isoformat(), previous))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (_digest(token), user_id, expires.isoformat()))
        return token

    def current_user(self, token: bytes | None) -> sqlite3.Row | None:
        if token is None:
            return None
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token=? AND s.expires_at>?", (token, utc_now())
            ).fetchone()

    def end_session(self, token: bytes | None) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM sessions WHERE token=?", (token,))


class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(min_length=8, max_length=128)

    def normalized_username(self) -> str:
        username = unicodedata.normalize("NFKC", self.username).strip()
        if not re.fullmatch(r"[\w.-]{2,32}", username):
            raise HTTPException(422, "用户名需为 2–32 个字母、数字、下划线、点或短横线。")
        return username


class CoursePlayback(BaseModel):
    playback_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def _same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin is not None and origin != expected:
        raise HTTPException(403, "请在 LivelyClass 页面操作。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请在 LivelyClass 页面操作。")


def course_routes(library: CourseLibrary) -> APIRouter:
    router = APIRouter(prefix="/api")

    def no_store(response: Response) -> None:
        response.headers.update(PRIVATE_CACHE)

    def user_data(user: sqlite3.Row) -> dict:
        return {"id": user["id"], "username": user["username"]}

    def require_user(request: Request, message: str) -> sqlite3.Row:
        user = library.current_user(_cookie_digest(request, SESSION_COOKIE))
        if user is None:
            raise HTTPException(401, message, headers=PRIVATE_CACHE)
        return user

    def set_session(request: Request, response: Response, user: sqlite3.Row) -> dict:
        token = library.start_session(user["id"], _cookie_digest(request, SESSION_COOKIE))
        no_store(response)
        _set_cookie(request, response, SESSION_COOKIE, token, SESSION_SECONDS)
        return {"user": user_data(user)}

    @router.get("/auth/session")
    def session(request: Request, response: Response) -> dict:
        no_store(response)
        user = library.current_user(_cookie_digest(request, SESSION_COOKIE))
        return {"user": user_data(user) if user else None}

    @router.post("/auth/register")
    def register(data: Credentials, request: Request, response: Response) -> dict:
        _same_origin(request)
        user = library.register(data.normalized_username(), data.password)
        return set_session(request, response, user)

    @router.post("/auth/login")
    def login(data: Credentials, request: Request, response: Response) -> dict:
        _same_origin(request)
        user = library.authenticate(data.normalized_username(), data.password)
        return set_session(request, response, user)

    @router.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict:
        _same_origin(request)
        library.end_session(_cookie_digest(request, SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, httponly=True, secure=request.url.scheme == "https", samesite="lax")
        no_store(response)
        return {"user": None}

    @router.get("/courses/mine")
    def my_courses(request: Request, response: Response) -> dict:
        user = require_user(request, "登录后即可查看我的课程。")
        no_store(response)
        return {
            "courses": library.list(user_id=user["id"]),
            "legacy_count": library.legacy_count(_cookie_digest(request, BROWSER_COOKIE)),
        }

    @router.post("/courses/import-browser")
    def import_browser_courses(request: Request, response: Response) -> dict:
        _same_origin(request)
        user = require_user(request, "登录后即可导入旧课程。")
        no_store(response)
        return {"course_ids": library.import_browser(_cookie_digest(request, BROWSER_COOKIE), user["id"])}

    @router.post("/jobs/{job_id}/save")
    def save_course(job_id: str, request: Request, response: Response) -> dict:
        _same_origin(request)
        user = require_user(request, "登录后即可保存课程。")
        no_store(response)
        return library.save(job_id, _cookie_digest(request, BROWSER_COOKIE), user["id"])

    @router.post("/jobs/{job_id}/publish")
    def publish_course(job_id: str, request: Request, response: Response) -> dict:
        _same_origin(request)
        user = require_user(request, "登录后即可分享课程。")
        no_store(response)
        return library.publish(job_id, _cookie_digest(request, BROWSER_COOKIE), user["id"])

    def get_course(course_id: str, request: Request, public: bool, *, detail: bool = False) -> dict:
        user_id = None if public else require_user(request, "登录后即可查看我的课程。")["id"]
        course = library.get(course_id, user_id=user_id, public=public, detail=detail)
        if course is None:
            raise HTTPException(404, "课程不存在。", headers=PRIVATE_CACHE)
        return course

    @router.get("/courses/{course_id}")
    def my_course(course_id: str, request: Request, response: Response) -> dict:
        no_store(response)
        return get_course(course_id, request, False, detail=True)

    @router.get("/community/courses")
    def community_courses(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-cache"
        return {"courses": library.list(public=True)}

    @router.get("/community/courses/{course_id}")
    def community_course(course_id: str, request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-cache"
        return get_course(course_id, request, True, detail=True)

    @router.post("/community/courses/{course_id}/views")
    def record_course_view(course_id: str, data: CoursePlayback, request: Request, response: Response) -> dict:
        _same_origin(request)
        no_store(response)
        return {"view_count": library.record_view(course_id, data.playback_id)}

    def course_asset(course_id: str, kind: str, request: Request, public: bool, download: bool) -> FileResponse:
        course = get_course(course_id, request, public)
        if not course["assets"].get(kind):
            raise HTTPException(404, "课程文件不存在。", headers=PRIVATE_CACHE)
        filename, mime = ASSETS[kind]
        path = library.directory / course_id / filename
        if not path.is_file():
            raise HTTPException(404, "课程文件不存在。", headers=PRIVATE_CACHE)
        headers = {"X-Content-Type-Options": "nosniff"}
        if not public:
            headers.update(PRIVATE_CACHE)
        return FileResponse(path, media_type=mime, filename=filename, headers=headers,
                            content_disposition_type="attachment" if download or kind == "presentation" else "inline")

    @router.get("/courses/{course_id}/assets/{kind}")
    def my_course_asset(course_id: str, kind: str, request: Request, download: bool = False) -> FileResponse:
        return course_asset(course_id, kind, request, False, download)

    @router.get("/community/courses/{course_id}/assets/{kind}")
    def community_course_asset(course_id: str, kind: str, request: Request, download: bool = False) -> FileResponse:
        return course_asset(course_id, kind, request, True, download)

    return router
