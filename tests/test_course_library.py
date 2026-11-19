import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from zhijiang.config import Settings
from zhijiang.courses import BROWSER_COOKIE, SESSION_COOKIE, _digest
from zhijiang.main import create_app
from zhijiang.models import Lesson, Mode, VoiceMode


class ManualRunner:
    def submit(self, job_id):
        pass

    def shutdown(self):
        pass


TRANSCRIPT = [
    "在有序数组里查找目标之前，我们先确定初始搜索范围。",
    "每次比较中点之后，搜索范围会缩小，直到找到目标或排除所有候选项。",
]


def complete_job(store, job_id):
    lesson = Lesson(
        title="二分查找",
        objective="用一段示例讲清楚如何逐步缩小搜索范围。",
        mode=Mode.DEMO,
        voice_mode=VoiceMode.SYSTEM,
        notice="internal-generation-notice",
        segments=[
            {
                "title": f"第{index}部分",
                "kind": "process",
                "narration": narration,
                "bullets": ["比较中点"],
                "evidence": {"page": index, "quote": "unpublished-source-quote"},
            }
            for index, narration in enumerate(TRANSCRIPT, 1)
        ],
        deck_plan={
            "learning_plan": {
                "overview": "安排一天复习。",
                "profile": {"minutes_per_day": 15, "background": "private-learner-background"},
                "sessions": [{
                    "day": 1, "title": "阅读算法例子", "minutes": 15, "activities": ["手算一次查找"],
                    "self_check": "说明何时终止。", "source_excerpt": "unpublished-source-quote",
                }],
                "total_minutes": 15,
                "catalogue": ["unpublished-source-quote"],
            },
        },
    )
    store.save_lesson(job_id, lesson)
    folder = store.jobs_dir / job_id
    (folder / "lesson.mp4").write_bytes(b"fixture-mp4-video")
    (folder / "lesson.pptx").write_bytes(b"fixture-pptx-slides")
    (folder / "presentation-preview").mkdir()
    (folder / "presentation-preview" / "page-001.png").write_bytes(b"fixture-cover")
    store.complete(job_id)
    return folder


def test_account_save_publication_and_archival(tmp_path, sample_pdf):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, ManualRunner())
    credentials = {"username": "测试讲师", "password": "example-instructor-password"}
    competitor = {"username": "另一位用户", "password": "example-competitor-password"}
    with TestClient(app) as owner, closing(TestClient(app)) as visitor:
        page = owner.get("/")
        assert BROWSER_COOKIE in page.headers["set-cookie"]
        assert "HttpOnly" in page.headers["set-cookie"]
        app_upload = owner.post(
            "/api/jobs", files={"file": ("lesson.pdf", sample_pdf, "application/pdf")},
            data={"mode": "demo", "rights_confirmed": "true"},
        )
        assert app_upload.status_code == 202, app_upload.text
        job = app_upload.json()
        assert "browser_owner" not in job
        job_url = f"/api/jobs/{job['id']}"
        assert owner.get("/api/auth/session").json() == {"user": None}
        assert owner.get("/api/courses/mine").status_code == 401
        assert owner.post(f"{job_url}/save").status_code == 401
        assert owner.post(f"{job_url}/publish").status_code == 401
        auth = owner.post("/api/auth/register", json=credentials)
        user = auth.json()["user"]
        assert user["username"] == credentials["username"] and isinstance(user["id"], int)
        assert owner.get("/api/auth/session").json() == {"user": user}
        assert SESSION_COOKIE in auth.headers["set-cookie"]
        assert "HttpOnly" in auth.headers["set-cookie"]
        assert owner.post(f"{job_url}/save").status_code == 409

        folder = complete_job(app.state.store, job["id"])
        (folder / "subtitles.json").write_text(json.dumps({"cues": [
            {"text": TRANSCRIPT[0], "start": 1.35, "end": 4.2},
            {"text": TRANSCRIPT[1], "start": 4.2, "end": 8.1},
        ]}), encoding="utf-8")
        # Existing web drafts still have their job-ID read links until archived. A different browser
        # cannot turn the draft into a saved or published course under its own account.
        assert visitor.get(job_url).status_code == 200
        assert visitor.post("/api/auth/register", json=competitor).status_code == 200
        assert visitor.post(f"{job_url}/save").status_code == 404
        assert visitor.post(f"{job_url}/publish").status_code == 404

        saved = owner.post(f"{job_url}/save")
        assert saved.status_code == 200, saved.text
        assert saved.headers["Cache-Control"] == "private, no-store"
        course = saved.json()
        assert owner.post(f"{job_url}/save").json() == course
        assert owner.get("/api/courses/mine").json() == {"courses": [course], "legacy_count": 0}

        private_url = f"/api/courses/{course['id']}"
        public_url = f"/api/community/courses/{course['id']}"
        assert visitor.get("/api/courses/mine").json() == {"courses": [], "legacy_count": 0}
        assert visitor.get(private_url).status_code == 404
        assert visitor.get(private_url).headers["Cache-Control"] == "private, no-store"
        for asset in course["assets"].values():
            assert visitor.get(asset).status_code == 404
        assert visitor.get(public_url).status_code == 404

        # A second account on the original browser cannot read or rebind the saved copy.
        browser_token = owner.cookies.get(BROWSER_COOKIE)
        visitor.cookies.set(BROWSER_COOKIE, browser_token)
        assert visitor.post(f"{job_url}/save").status_code == 404
        assert visitor.post(f"{job_url}/publish").status_code == 404
        assert visitor.get(job_url).status_code == 404
        assert visitor.get(f"{job_url}/lesson").status_code == 404
        assert visitor.get(f"{job_url}/video").status_code == 404
        assert visitor.get(f"{job_url}/presentation").status_code == 404
        assert visitor.delete(job_url).status_code == 404
        assert owner.get(f"{job_url}/video").status_code == 200

        assert visitor.post("/api/auth/logout").json() == {"user": None}
        for path in ("/api/courses/mine", private_url, course["assets"]["video"]):
            denied = visitor.get(path)
            assert denied.status_code == 401
            assert denied.headers["Cache-Control"] == "private, no-store"
        assert visitor.get(f"{job_url}/video").status_code in (401, 404)

        assert owner.post(f"{job_url}/publish", headers={"Origin": "https://example.invalid"}).status_code == 403
        published = owner.post(f"{job_url}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["id"] == course["id"]
        assert published.json()["author"] == credentials["username"]
        assert owner.post(f"{job_url}/publish").json() == published.json()

        assert visitor.get("/api/community/courses").json() == {"courses": [published.json()]}
        public = visitor.get(public_url)
        assert public.status_code == 200, public.text
        details = public.json()
        assert details["transcript"] == [
            {"text": TRANSCRIPT[0], "start_seconds": 1.35, "end_seconds": 4.2},
            {"text": TRANSCRIPT[1], "start_seconds": 4.2, "end_seconds": 8.1},
        ]
        assert details["learning_plan"]["profile"] == {"minutes_per_day": 15}
        assert details["learning_plan"]["sessions"][0]["activities"] == ["手算一次查找"]
        for private_value in ("private-learner-background", "unpublished-source-quote",
                              "internal-generation-notice", job["id"], "lesson.pdf", credentials["password"]):
            assert private_value not in public.text
        assert visitor.get(f"{public_url}/assets/source.pdf").status_code == 404
        video = visitor.get(details["assets"]["video"], headers={"Range": "bytes=0-6"})
        assert video.status_code == 206 and video.content == b"fixture"
        download = visitor.get(details["assets"]["video"], params={"download": True})
        assert download.headers["content-disposition"].startswith("attachment")
        assert visitor.get(details["assets"]["presentation"]).content == b"fixture-pptx-slides"
        assert visitor.get(details["assets"]["cover"]).content == b"fixture-cover"
        assert visitor.get(f"{job_url}/video").status_code in (401, 404)

        assert owner.delete(job_url).status_code == 204
        assert not folder.exists()
        assert owner.get(private_url).status_code == 200
        assert owner.post("/api/auth/logout").json() == {"user": None}
        assert owner.get("/api/courses/mine").status_code == 401

    restarted = create_app(settings, ManualRunner())
    with TestClient(restarted) as browser:
        # Persistence works even without the browser cookie that created the job.
        assert browser.get(private_url).status_code == 401
        assert browser.get(public_url).json()["assets"] == details["assets"]
        assert browser.post("/api/auth/login", json={**credentials, "password": "wrong password"}).status_code == 401
        assert browser.post("/api/auth/login", json=credentials).json() == auth.json()
        assert browser.get("/api/courses/mine").json()["courses"][0]["id"] == course["id"]
        assert browser.get(private_url).json()["transcript"] == details["transcript"]
        assert browser.get(course["assets"]["video"]).content == b"fixture-mp4-video"
        assert browser.get(course["assets"]["video"]).headers["Cache-Control"] == "private, no-store"
        assert browser.post(f"{job_url}/publish").json() == published.json()
        assert browser.post(f"{job_url}/save").json()["id"] == course["id"]


def test_import_claims_only_legacy_courses_in_the_original_browser(tmp_path, sample_pdf):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, ManualRunner())
    credentials = {"username": "迁移课程", "password": "migration test password"}
    # Seed the browser-owned rows and files exactly as the old version stored them.
    store = app.state.store
    old_job = store.create("legacy.pdf", Mode.DEMO, VoiceMode.SYSTEM, True, False, sample_pdf)
    complete_job(store, old_job["id"])
    course_id = "a" * 32
    archive = settings.data_dir / "courses" / course_id
    archive.mkdir()
    (archive / "lesson.mp4").write_bytes(b"legacy video")
    (archive / "lesson.pptx").write_bytes(b"legacy presentation")
    job_url = f"/api/jobs/{old_job['id']}"
    assert store.delete(old_job["id"])
    with TestClient(app) as original, closing(TestClient(app)) as other:
        original.get("/")
        browser_token = original.cookies.get(BROWSER_COOKIE)
        with closing(sqlite3.connect(settings.data_dir / "courses.sqlite3")) as db, db:
            db.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?)",
                       (course_id, old_job["id"], "旧课程", "保留原保存记录", "2026-01-01", False, "{}"))
            db.execute("INSERT INTO saves VALUES (?,?,?)", (_digest(browser_token), course_id, "2026-01-01"))
        assert original.post("/api/courses/import-browser").status_code == 401
        assert other.post("/api/auth/register", json=credentials).status_code == 200
        assert other.get("/api/courses/mine").json() == {"courses": [], "legacy_count": 0}
        assert other.post("/api/courses/import-browser").json() == {"course_ids": []}

        assert original.post("/api/auth/login", json=credentials).status_code == 200
        assert original.get("/api/courses/mine").json() == {"courses": [], "legacy_count": 1}
        assert original.get(f"/api/courses/{course_id}/assets/video").status_code == 404
        assert original.post("/api/courses/import-browser").json() == {"course_ids": [course_id]}
        assert original.post("/api/courses/import-browser").json() == {"course_ids": []}
        assert original.get("/api/courses/mine").json()["legacy_count"] == 0
        assert other.get(f"/api/courses/{course_id}/assets/video").content == b"legacy video"
        assert other.post(f"{job_url}/publish").json()["id"] == course_id

        original.post("/api/auth/logout")
        assert original.post("/api/auth/register", json={"username": "另一个账号", "password": "someone else"}).status_code == 200
        assert original.get("/api/courses/mine").json() == {"courses": [], "legacy_count": 0}
        assert original.get(f"/api/courses/{course_id}/assets/video").status_code == 404
        assert original.post(f"{job_url}/publish").status_code == 404
        assert original.get(f"/api/community/courses/{course_id}/assets/video").content == b"legacy video"


def test_community_playbacks_count_each_detail_open_once(tmp_path, sample_pdf):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, ManualRunner())
    with (TestClient(app) as owner, closing(TestClient(app)) as viewer,
          closing(TestClient(app)) as other_viewer):
        upload = owner.post(
            "/api/jobs", files={"file": ("lesson.pdf", sample_pdf, "application/pdf")},
            data={"mode": "demo", "rights_confirmed": "true"},
        )
        assert upload.status_code == 202
        job_url = f"/api/jobs/{upload.json()['id']}"
        complete_job(app.state.store, upload.json()["id"])
        assert owner.post("/api/auth/register", json={"username": "讲师", "password": "test password"}).status_code == 200
        course = owner.post(f"{job_url}/save").json()
        course_url = f"/api/community/courses/{course['id']}"
        view_url = f"{course_url}/views"
        first_playback = {"playback_id": "a" * 32}
        assert viewer.post(view_url, json=first_playback).status_code == 404

        assert owner.post(f"{job_url}/publish").json()["view_count"] == 0
        assert viewer.get(course_url).json()["view_count"] == 0
        assert other_viewer.get(course_url).json()["view_count"] == 0
        assert viewer.get(f"{course_url}/assets/video").status_code == 200
        assert viewer.get("/api/community/courses").json()["courses"][0]["view_count"] == 0
        assert viewer.post(view_url, json={"playback_id": "invalid"}).status_code == 422
        assert viewer.post(view_url, json=first_playback, headers={"Origin": "https://elsewhere.invalid"}).status_code == 403
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: viewer.post(view_url, json=first_playback), range(3)))
        assert [result.json() for result in results] == [{"view_count": 1}] * 3
        assert other_viewer.post(view_url, json={"playback_id": "b" * 32}).json() == {"view_count": 2}
        assert viewer.get(course_url).json()["view_count"] == 2

    with TestClient(create_app(settings, ManualRunner())) as viewer:
        assert viewer.get("/api/community/courses").json()["courses"][0]["view_count"] == 2
        assert viewer.post(view_url, json=first_playback).json() == {"view_count": 2}


@pytest.mark.parametrize("has_timeline", (False, True))
def test_legacy_course_without_subtitle_file(tmp_path, sample_pdf, has_timeline):
    app = create_app(Settings(data_dir=tmp_path / "data"), ManualRunner())
    store = app.state.store
    legacy = store.create("legacy.pdf", Mode.DEMO, VoiceMode.SYSTEM, True, False, sample_pdf)
    folder = complete_job(store, legacy["id"])
    lesson = store.lesson(legacy["id"])
    lesson.deck_plan.clear()
    store.save_lesson(legacy["id"], lesson)
    if has_timeline:
        (folder / "video-timeline.json").write_text(json.dumps({
            "bookends": [{"kind": "cover", "duration": 3.0}],
            "segments": [{"segment_id": 1, "duration": 5.0}, {"segment_id": 2, "duration": 7.0}],
        }), encoding="utf-8")
    with TestClient(app) as browser:
        assert browser.post("/api/auth/register", json={"username": "旧课程讲师", "password": "test password"}).status_code == 200
        saved = browser.post(f"/api/jobs/{legacy['id']}/save")
        assert saved.status_code == 200, saved.text
        result = browser.get(f"/api/courses/{saved.json()['id']}").json()
        assert result["learning_plan"] is None
        intervals = [(3.0, 8.0), (8.0, 15.0)] if has_timeline else [(None, None)] * 2
        assert result["transcript"] == [
            {"text": text, "start_seconds": start, "end_seconds": end}
            for text, (start, end) in zip(TRANSCRIPT, intervals)
        ]
