"""Input integrity checks for the explicitly selected demonstration cases."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts/premium_demo_inputs.py"
    spec = importlib.util.spec_from_file_location("premium_demo_inputs", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _source(module, factory):
    if factory == "build_math":
        return module.build_math(blocks=[(1, "definition", "这是一个完整的通用测试句子。"), (2, "question", "测试问题如何保留？")])
    return module.build_humanities()


def test_poem_exactly_once_in_eight_ordered_source_units():
    m = _module()
    source = m.build_humanities()
    poem = [x["text"] for x in source["units"] if x["kind"] == "poem_line"]
    assert poem == ["空山新雨后，", "天气晚来秋。", "明月松间照，", "清泉石上流。", "竹喧归浣女，", "莲动下渔舟。", "随意春芳歇，", "王孙自可留。"]
    assert len([x for x in source["units"] if x["kind"] == "appreciation"]) == 4
    assert 250 <= sum(len(x[1]) for x in m.APPRECIATION) <= 350
    assert source["provenance"]["not_pep_textbook"] is True


@pytest.mark.parametrize("factory", ["build_humanities", "build_math"])
def test_source_validator_rejects_omission_and_reordering(factory):
    m = _module()
    source = _source(m, factory)
    m.validate_source(source)
    broken = copy.deepcopy(source)
    broken["units"] = broken["units"][1:]
    with pytest.raises(ValueError, match="preserve"):
        m.validate_source(broken)
    broken = copy.deepcopy(source)
    broken["units"][0], broken["units"][1] = broken["units"][1], broken["units"][0]
    with pytest.raises(ValueError, match="preserve"):
        m.validate_source(broken)


def test_math_original_identities_and_condition_associations_retained():
    m = _module()
    if not m.MATH_SEED.is_file():
        pytest.skip("private authorized textbook transcription is not distributed with repository")
    source = m.build_math()
    text = source["source_text"]
    for value in ["∠C=90°，AC=5，BC=12", "边AB的中点D", "r=2.5", "DE=½BC，DF=½AC", "DE>r，DF=r", "AC与⊙D相离，BC与⊙D相切"]:
        assert value in text
    assert source["provenance"]["native_text"] is False
    assert source["provenance"]["independent_ocr_accuracy_gold"] is False
    assert any(x["kind"] == "sidebar" for x in source["units"])
    assert any(x["kind"] == "question" and x["printed_page"] == 148 for x in source["units"])
    assert any(x["kind"] == "question" and x["printed_page"] == 149 for x in source["units"])


def test_spoken_notation_does_not_modify_source_and_is_explicit():
    m = _module()
    original = "∴ AC与⊙D相离，BC与⊙D相切。"
    spoken, changes = m.math_spoken(original)
    assert original == "∴ AC与⊙D相离，BC与⊙D相切。"
    assert spoken == "所以 AC与圆D相离，BC与圆D相切。"
    assert {x["source"] for x in changes} == {"∴", "⊙D"}
    figure, _ = m.math_spoken("如图30.1-4")
    assert figure == "如图三十点一杠四"


def test_learner_suggestions_reference_only_existing_source_units():
    m = _module()
    profile = m.build_profile()
    generic_math = m.build_math(blocks=[(1, "definition", f"第{i}个通用测试单元。") for i in range(22)])
    for key, source in [("math", generic_math), ("humanities", m.build_humanities())]:
        valid = {x["unit_id"] for x in source["units"]}
        activities = profile["learner_profiles"][key]["suggested_activities"]
        referenced = {i for x in activities for i in x["unit_ids"]}
        assert referenced == valid
    assert "真实segment_id" in m.build_prompts()["I_learning_plan"]


def test_stage_roles_and_local_image_speech_are_explicit():
    profile = _module().build_profile()
    assert profile["llm"]["allow_official_deepseek_for_this_demo"]
    assert profile["llm"]["roles"]["math_construction"] == "max"
    assert profile["llm"]["roles"]["deck_planning"] == "high"
    assert profile["images"]["steps"] == 16
    assert profile["images"]["local_upsampler"] is False
    assert profile["tts"]["sample_rate"] == 48000
    assert profile["video"]["slide_audio_video_embedded"] is False


def test_missing_private_math_input_does_not_fabricate_text(tmp_path):
    with pytest.raises(FileNotFoundError, match="Private mathematics input seed"):
        _module().build_math(seed_path=tmp_path / "not-provided.json")
