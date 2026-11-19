import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from zhijiang.config import Settings
from zhijiang.courses import BROWSER_COOKIE, SESSION_COOKIE
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


def test_browser_save_publication_and_archival(tmp_path, sample_pdf):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, ManualRunner())
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
        assert owner.post(f"{job_url}/save").status_code == 409

        folder = complete_job(app.state.store, job["id"])
        (folder / "subtitles.json").write_text(json.dumps({"cues": [
            {"text": TRANSCRIPT[0], "start": 1.35, "end": 4.2},
            {"text": TRANSCRIPT[1], "start": 4.2, "end": 8.1},
        ]}), encoding="utf-8")
        assert owner.get("/api/auth/session").json() == {"user": None}
        assert owner.post(f"{job_url}/publish").status_code == 401
        saved = owner.post(f"{job_url}/save")
        assert saved.status_code == 200, saved.text
        course = saved.json()
        assert owner.post(f"{job_url}/save").json() == course
        assert owner.get("/api/courses/mine").json() == {"courses": [course]}

        private_url = f"/api/courses/{course['id']}"
        public_url = f"/api/community/courses/{course['id']}"
        assert visitor.get("/api/courses/mine").json() == {"courses": []}
        assert visitor.get(private_url).status_code == 404
        assert visitor.get(course["assets"]["video"]).status_code == 404
        assert visitor.get(public_url).status_code == 404
        # Old read-only job links still behave as before. New archive creation is browser-bound.
        assert visitor.get(job_url).status_code == 200
        assert visitor.post(f"{job_url}/save").status_code == 404
        competitor = {"username": "另一位用户", "password": "example-competitor-password"}
        assert visitor.post("/api/auth/register", json=competitor).status_code == 200
        assert visitor.post(f"{job_url}/publish").status_code == 404
        assert visitor.post("/api/auth/logout").json() == {"user": None}

        credentials = {"username": "测试讲师", "password": "example-instructor-password"}
        auth = owner.post("/api/auth/register", json=credentials)
        assert auth.json() == {"user": {"username": credentials["username"]}}
        assert SESSION_COOKIE in auth.headers["set-cookie"]
        assert "HttpOnly" in auth.headers["set-cookie"]
        assert owner.post(f"{job_url}/publish", headers={"Origin": "https://example.invalid"}).status_code == 403
        published = owner.post(f"{job_url}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["id"] == course["id"]
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

        assert owner.delete(job_url).status_code == 204
        assert not folder.exists()
        assert owner.post(f"{job_url}/publish").json() == published.json()
        assert owner.post(f"{job_url}/save").json()["id"] == course["id"]
        assert owner.get(private_url).status_code == 200
        assert owner.post("/api/auth/logout").json() == {"user": None}
        assert owner.get("/api/courses/mine").json()["courses"][0]["id"] == course["id"]
        browser_token = owner.cookies.get(BROWSER_COOKIE)

    restarted = create_app(settings, ManualRunner())
    with TestClient(restarted, cookies={BROWSER_COOKIE: browser_token}) as browser:
        assert browser.get(private_url).json()["transcript"] == details["transcript"]
        assert browser.get(public_url).json()["assets"] == details["assets"]
        assert browser.get(course["assets"]["video"]).content == b"fixture-mp4-video"
        assert browser.post("/api/auth/login", json={**credentials, "password": "wrong password"}).status_code == 401
        assert browser.post("/api/auth/login", json=credentials).json() == auth.json()


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
        saved = browser.post(f"/api/jobs/{legacy['id']}/save")
        assert saved.status_code == 200, saved.text
        result = browser.get(f"/api/courses/{saved.json()['id']}").json()
        assert result["learning_plan"] is None
        intervals = [(3.0, 8.0), (8.0, 15.0)] if has_timeline else [(None, None)] * 2
        assert result["transcript"] == [
            {"text": text, "start_seconds": start, "end_seconds": end}
            for text, (start, end) in zip(TRANSCRIPT, intervals)
        ]
