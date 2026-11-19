"""Source identity, selected-media and measured timing contract checks."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import wave

from PIL import Image
import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "scripts/premium_demo_humanities.py"
    spec = importlib.util.spec_from_file_location("premium_demo_humanities", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def data(tmp_path):
    units = [{"unit_id": "a", "text": "示例甲。", "kind": "poem_line"},
             {"unit_id": "b", "text": "第二段是测试用的原文。", "kind": "appreciation"}]
    source = {"units": units, "unit_separator": "\n", "source_text": "示例甲。\n第二段是测试用的原文。"}
    story = {"shots": [{"unit_id": "a", "scene_title": "甲", "keywords": []}, {"unit_id": "b", "scene_title": "乙", "keywords": []}]}
    master = tmp_path / "master.wav"
    with wave.open(str(master), "wb") as w:
        w.setparams((1, 2, 48000, 192000, "NONE", "none"))
        w.writeframes(b"\0" * 384000)
    timeline = {"sample_rate": 48000, "samples": 192000, "complete": True,
                "source_text": source["source_text"], "master_sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                "units": [{**units[0], "start": 0, "speech_end": 1.5, "end": 2}, {**units[1], "start": 2, "speech_end": 3.5, "end": 4}]}
    im = tmp_path / "selected.png"
    Image.new("RGB", (1280, 704), (20, 40, 50)).save(im)
    selected = {"a": str(im.resolve()), "b": str(im.resolve())}
    return source, story, timeline, selected, master


def test_full_source_and_actual_pcm_duration_match(data):
    rows = module().validate_inputs(*data)
    assert len(rows) == 2
    assert rows[1]["timing"]["start"] == 2


@pytest.mark.parametrize("variant", ["missing", "relative", "corrupt"])
def test_missing_or_unusable_selected_picture_rejected(data, variant, tmp_path):
    data = list(copy.deepcopy(data))
    if variant == "missing":
        data[3].pop("a")
    elif variant == "relative":
        data[3]["a"] = "selected.png"
    else:
        path = tmp_path / "broken.png"
        path.write_text("not an image")
        data[3]["a"] = str(path.resolve())
    with pytest.raises((ValueError, OSError)):
        module().validate_inputs(*data)


@pytest.mark.parametrize("change", ["duplicate", "order", "text", "overlap", "samples", "master"])
def test_altered_or_repeated_source_and_false_timing_rejected(data, change):
    args = list(copy.deepcopy(data))
    if change == "duplicate":
        args[2]["units"].append(args[2]["units"][0])
    elif change == "order":
        args[1]["shots"].reverse()
    elif change == "text":
        args[2]["units"][0]["text"] = "改写的概括。"
    elif change == "overlap":
        args[2]["units"][1]["start"] = 1.8
    elif change == "samples":
        args[2]["samples"] += 100
    else:
        args[2]["master_sha256"] = "not the measured audio"
    with pytest.raises(ValueError):
        module().validate_inputs(*args)


def test_prose_cues_cover_exact_character_slices_and_times(data):
    m = module()
    rows = m.validate_inputs(*data)
    rows[1]["text"] = "这是第一句测试文字，保留标点与所有字符。" * 6
    cues = [x for x in m.make_cues(rows) if x["unit_id"] == "b"]
    assert "".join(x["text"] for x in cues) == rows[1]["text"]
    assert cues[0]["start"] == 2
    assert cues[-1]["end"] == 4
    assert all(a["end"] == b["start"] for a, b in zip(cues, cues[1:]))
    assert all("proportional" in x["timing_method"] for x in cues)


def test_ass_injection_and_two_line_readability():
    m = module()
    assert m.ass_escape("{\\pos(1,1)}原文") == "｛＼pos(1,1)｝原文"
    text = "这是一句需要保持每个字符和标点的字幕，换行只影响显示。" * 2
    wrapped = m.wrapped(text, 38, 1500)
    assert wrapped.replace("\\N", "") == text
