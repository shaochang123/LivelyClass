"""单机任务状态与文件目录。每次操作使用独立 SQLite 连接。"""

from __future__ import annotations

import json
import sqlite3
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from zhijiang.models import GenerationOptions, JobStatus, Lesson, Mode, SpeechOptions, VoiceMode


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.db_path = data_dir / "jobs.sqlite3"
        self._api_keys: dict[str, str] = {}
        self._tts_api_keys: dict[str, str] = {}
        self._art_api_keys: dict[str, dict[str, str]] = {}
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    voice_mode TEXT NOT NULL,
                    rights_confirmed INTEGER NOT NULL,
                    remote_consent INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    error TEXT,
                    lesson_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    options_json TEXT NOT NULL DEFAULT '{}',
                    speech_options_json TEXT NOT NULL DEFAULT '{}'
                )"""
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            if "options_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'")
            if "speech_options_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN speech_options_json TEXT NOT NULL DEFAULT '{}'")

    def recover_interrupted(self) -> None:
        """Recover stale jobs once at service startup, never on import/read."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, stage=?, error=?, updated_at=? "
                "WHERE status IN (?, ?)",
                (
                    JobStatus.FAILED,
                    "interrupted",
                    "服务中断，请重新提交任务。",
                    utc_now(),
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                ),
            )

    def create(
        self,
        filename: str,
        mode: Mode,
        voice_mode: VoiceMode,
        rights_confirmed: bool,
        remote_consent: bool,
        pdf: bytes,
        options: GenerationOptions | None = None,
        speech_options: SpeechOptions | None = None,
    ) -> dict:
        job_id = uuid.uuid4().hex
        folder = self.jobs_dir / job_id
        folder.mkdir()
        (folder / "source.pdf").write_bytes(pdf)
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs (id, filename, mode, voice_mode, rights_confirmed, "
                "remote_consent, status, stage, progress, error, lesson_json, "
                "created_at, updated_at, options_json, speech_options_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id, filename, mode, voice_mode, int(rights_confirmed),
                    int(remote_consent), JobStatus.QUEUED, "queued", 0, None,
                    None, now, now, (options or GenerationOptions()).model_dump_json(),
                    (speech_options or SpeechOptions()).model_dump_json(),
                ),
            )
        if options and options.api_key:
            self._api_keys[job_id] = options.api_key
        if speech_options and speech_options.api_key:
            self._tts_api_keys[job_id] = speech_options.api_key
        if options:
            self.set_art_api_keys(job_id, options.art)
        return self.get(job_id)  # type: ignore[return-value]

    def get_options(self, job_id: str) -> GenerationOptions:
        with self._connect() as connection:
            row = connection.execute("SELECT options_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        options = GenerationOptions.model_validate_json(row["options_json"] if row else "{}")
        options.api_key = self._api_keys.get(job_id, "")
        for kind in ('image', 'video'):
            getattr(options.art, kind).api_key = self._art_api_keys.get(job_id, {}).get(kind, '')
        return options

    def set_art_api_keys(self, job_id, art):
        self._art_api_keys[job_id] = {kind: getattr(art, kind).api_key for kind in ('image', 'video')}

    def set_api_key(self, job_id: str, api_key: str) -> None:
        if api_key:
            self._api_keys[job_id] = api_key

    def get_speech_options(self, job_id: str) -> SpeechOptions:
        with self._connect() as connection:
            row = connection.execute("SELECT speech_options_json FROM jobs WHERE id=?", (job_id,)).fetchone()
        options = SpeechOptions.model_validate_json(row["speech_options_json"] if row else "{}")
        options.api_key = self._tts_api_keys.get(job_id, "")
        return options

    def set_tts_api_key(self, job_id: str, api_key: str) -> None:
        if api_key:
            self._tts_api_keys[job_id] = api_key

    def update_speech_options(self, job_id: str, options: SpeechOptions) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE jobs SET speech_options_json=? WHERE id=?",
                               (options.model_dump_json(), job_id))

    def update_options(self, job_id: str, options: GenerationOptions) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE jobs SET options_json=? WHERE id=?",
                               (options.model_dump_json(), job_id))

    def clear_api_key(self, job_id: str) -> None:
        self._api_keys.pop(job_id, None)
        self._tts_api_keys.pop(job_id, None)
        self._art_api_keys.pop(job_id, None)

    def get(self, job_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["rights_confirmed"] = bool(result["rights_confirmed"])
        result["remote_consent"] = bool(result["remote_consent"])
        result["has_lesson"] = result["lesson_json"] is not None
        result["has_video"] = (self.jobs_dir / job_id / "lesson.mp4").is_file()
        result["has_presentation"] = (self.jobs_dir / job_id / "lesson.pptx").is_file()
        result["has_math_scenes"] = (self.jobs_dir / job_id / "math-scenes.json").is_file()
        result["has_scenes"] = result["has_math_scenes"] or (self.jobs_dir/job_id/"scene-data.json").is_file()
        result.pop("lesson_json")
        result["model_settings"] = json.loads(result.pop("options_json"))
        result["voice_settings"] = json.loads(result.pop("speech_options_json"))
        return result

    def set_progress(self, job_id: str, stage: str, progress: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, stage=?, progress=?, updated_at=? WHERE id=?",
                (JobStatus.RUNNING, stage, progress, utc_now(), job_id),
            )

    def save_lesson(self, job_id: str, lesson: Lesson) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET lesson_json=?, updated_at=? WHERE id=?",
                (lesson.model_dump_json(), utc_now(), job_id),
            )

    def lesson(self, job_id: str) -> Lesson | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT lesson_json FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None or row["lesson_json"] is None:
            return None
        return Lesson.model_validate(json.loads(row["lesson_json"]))

    def complete(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, stage=?, progress=100, updated_at=? WHERE id=?",
                (JobStatus.COMPLETED, "completed", utc_now(), job_id),
            )

    def fail(self, job_id: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status=?, stage=?, error=?, updated_at=? WHERE id=?",
                (JobStatus.FAILED, "failed", message[:500], utc_now(), job_id),
            )

    def retry(self, job_id: str, *, mode: Mode | None = None,
              voice_mode: VoiceMode | None = None, options: GenerationOptions | None = None,
              speech_options: SpeechOptions | None = None, remote_consent: bool | None = None) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status=?, stage=?, progress=0, error=NULL, "
                "lesson_json=NULL, updated_at=?, mode=COALESCE(?,mode), "
                "voice_mode=COALESCE(?,voice_mode), options_json=COALESCE(?,options_json), "
                "speech_options_json=COALESCE(?,speech_options_json), "
                "remote_consent=COALESCE(?,remote_consent) WHERE id=? AND status=?",
                (JobStatus.QUEUED, "queued", utc_now(), mode, voice_mode,
                 options.model_dump_json() if options else None,
                 speech_options.model_dump_json() if speech_options else None,
                 remote_consent, job_id, JobStatus.FAILED),
            )
        if cursor.rowcount != 1:
            return False
        (self.jobs_dir / job_id / "lesson.mp4").unlink(missing_ok=True)
        (self.jobs_dir / job_id / "lesson.pptx").unlink(missing_ok=True)
        (self.jobs_dir / job_id / "math-scenes.json").unlink(missing_ok=True)
        (self.jobs_dir/job_id/"scene-data.json").unlink(missing_ok=True)
        for name in ("math","visual","presentation-preview"):
            scene_folder=(self.jobs_dir/job_id/name).resolve()
            if not scene_folder.is_relative_to(self.jobs_dir.resolve()):
                raise ValueError("场景素材路径不在任务目录内。")
            if scene_folder.is_dir():
                shutil.rmtree(scene_folder)
        return True

    def delete(self, job_id: str) -> bool:
        self.clear_api_key(job_id)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return cursor.rowcount > 0
