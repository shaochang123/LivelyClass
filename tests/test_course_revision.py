import json
from contextlib import closing

from fastapi.testclient import TestClient

from zhijiang.config import Settings
from zhijiang.literal_course import build_literal_course, verify_literal
from zhijiang.main import create_app
from zhijiang.models import CourseRevision, GenerationOptions, PageText, SourceDocument, VoiceMode


class ManualRunner:
    def __init__(self):
        self.submitted = []

    def submit(self, job_id):
        self.submitted.append(job_id)

    def shutdown(self):
        pass


def source_document():
    return SourceDocument(filename="搜索.pdf", pages=[
        PageText(page=1, text="在有序数组中选择中点，根据比较结果缩小范围。"),
        PageText(page=2, text="重复搜索过程，直到找到元素或确认元素不存在。"),
    ])


def finish_job(store, job_id, lesson):
    store.save_lesson(job_id, lesson)
    (store.jobs_dir / job_id / "lesson.mp4").write_bytes(b"previous video")
    store.clear_api_key(job_id)
    store.complete(job_id)


def test_revision_copies_source_and_material_with_owned_retry(tmp_path, sample_pdf):
    runner = ManualRunner()
    settings = Settings(
        data_dir=tmp_path / "data", llm_provider="ollama",
        llm_base_url="http://127.0.0.1:11434", llm_model="fixture",
        tts_base_url="https://voice.example/v1", tts_model="fixture-tts", tts_api_key="secret-tts",
    )
    app = create_app(settings, runner)
    store = app.state.store
    lesson = build_literal_course(
        source_document(), None, GenerationOptions(ppt_template="paper"), VoiceMode.AI,
        tmp_path / "original" / "slides", lambda *_: None,
    )
    with TestClient(app) as owner, closing(TestClient(app)) as visitor:
        initial = owner.post("/api/jobs", files={"file": ("搜索.pdf", sample_pdf, "application/pdf")}, data={
            "rights_confirmed": "true", "mode": "ai", "voice_mode": "ai", "remote_consent": "true",
            "narration_mode": "source_verbatim", "ppt_template": "paper",
            "llm_review_model": "review-fixture", "llm_math_model": "planning-fixture",
            "custom_prompt": "给初学者", "learner_enabled": "true", "learner_days": "5",
            "image_protocol": "images", "image_model": "fixture-image",
            "image_base_url": "https://images.example/v1", "image_api_key": "old-secret-image",
        })
        assert initial.status_code == 202, initial.text
        parent = initial.json()
        assert parent["parent_job_id"] is None
        assert runner.submitted == [parent["id"]]
        url = f"/api/jobs/{parent['id']}"
        request = {"custom_prompt": "把第二段 PPT 的示例写短一点", "image_api_key": "new-secret-image"}
        assert visitor.post(f"{url}/revise", data=request).status_code == 404
        assert owner.post(f"{url}/revise", headers={"Origin": "https://other.example"}, data=request).status_code == 403
        assert owner.post(f"{url}/revise", data=request).status_code == 409
        finish_job(store, parent["id"], lesson)
        parsed_source = store.jobs_dir / parent["id"] / "parsed-source.json"
        parsed_source.write_text("previous parsed source", encoding="utf-8")
        assert owner.post(f"{url}/revise", data={"custom_prompt": "  "}).status_code == 400
        assert owner.post(f"{url}/revise", data={**request, "remote_consent": "false"}).status_code == 400
        response = owner.post(f"{url}/revise", data=request)
        assert response.status_code == 202, response.text
        child = response.json()
        child_id = child["id"]
        assert child_id != parent["id"]
        assert child["parent_job_id"] == parent["id"]
        assert child["remote_consent"] is True
        assert child["model_settings"] == {**parent["model_settings"], "prompt": request["custom_prompt"]}
        assert child["voice_settings"] == parent["voice_settings"]
        assert "secret" not in response.text
        assert store.get_options(child_id).art.image.api_key == "new-secret-image"
        assert store.get_speech_options(child_id).api_key == "secret-tts"
        assert (store.jobs_dir / child_id / "source.pdf").read_bytes() == sample_pdf
        assert (store.jobs_dir / child_id / "parsed-source.json").read_bytes() == parsed_source.read_bytes()
        context = store.revision_context(child_id)
        assert context.lesson == lesson
        assert context.original_request == context.previous_request == "给初学者"
        assert owner.get(f"{url}/video").content == b"previous video"

        store.clear_api_key(child_id)
        store.fail(child_id, "fixture failure")
        retry_url = f"/api/jobs/{child_id}/retry"
        assert visitor.post(retry_url, data=request).status_code == 404
        response = owner.post(retry_url, data={**request, "custom_prompt": "different", "replace_settings": True})
        assert response.status_code == 202, response.text
        assert response.json()["model_settings"] == child["model_settings"]
        assert runner.submitted == [parent["id"], child_id, child_id]
        assert owner.delete(url).status_code == 204
        assert store.revision_context(child_id) == context

        finish_job(store, child_id, lesson)
        final = owner.post(f"/api/jobs/{child_id}/revise", data={**request, "custom_prompt": "加宽第一段图示"})
        assert final.status_code == 202, final.text
        final_context = store.revision_context(final.json()["id"])
        assert final_context.original_request == "给初学者"
        assert final_context.previous_request == request["custom_prompt"]
        assert final.json()["parent_job_id"] == child_id


def test_literal_revision_uses_previous_slide_and_preserves_source(tmp_path):
    doc = source_document()
    options = GenerationOptions(ppt_template="paper", prompt="缩短第二段示例")
    progress = lambda *_: None
    first = build_literal_course(doc, None, options, VoiceMode.SYSTEM, tmp_path / "first" / "slides", progress)
    context = CourseRevision(lesson=first, original_request="为初学者讲解", previous_request="用数组示例")

    class Client:
        def generate(self, schema, instruction, material):
            data = json.loads(material)
            segment = data["segment_number"]
            assert segment == 2
            assert data["segment_count"] == 2
            assert data["teaching_preferences"] == "缩短第二段示例"
            assert data["original_request"] == "为初学者讲解"
            assert data["previous_request"] == "用数组示例"
            assert data["previous_slide"]["title"] == first.segments[segment - 1].title
            assert "上一版" in instruction
            return schema(
                title="检查末尾范围",
                bullets=["空区间就结束"],
                visual_medium="svg",
                visual={"representation": "key_idea", "items": [
                    {"label": "检查范围", "caption": "比较源文本中的范围", "sentence_id": 1},
                ], "links": [], "context": ""},
                story_stage="理解示例", layout="wide",
            )

    revised = build_literal_course(
        doc, Client(), options, VoiceMode.SYSTEM, tmp_path / "second" / "slides", progress, revision=context,
    )
    assert revised.segments[0].title == first.segments[0].title
    assert revised.segments[1].title == "检查末尾范围"
    assert revised.segments[1].bullets == ["空区间就结束"]
    assert revised.segments[1].source_spans == first.segments[1].source_spans
    assert revised.segments[1].narration == first.segments[1].narration
    assert verify_literal(doc, revised)["verified"]
