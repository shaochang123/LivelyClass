"""Independent numeric and source-identity checks for the fixed showcase."""
import copy
import math
import json
import wave
import xml.etree.ElementTree as ET

import pytest

from zhijiang.demo_math import (DemoMathError, circle_line_geometry, circle_summary_svg, circle_comparison_svg,
                               bind_source_audio, default_plans, example_summary_svg, plan_schema,
                               exploration_segments, exploration_geometry_at, source_subtitle_spans,
                               textbook_geometry, validate_plan)


@pytest.mark.parametrize("distance,count", [(3, 0), (2, 1), (1, 2), (0, 2), (-2, 1), (-3, 0)])
def test_intersections_obey_both_equations(distance, count):
    data = circle_line_geometry((0, 0), 2, (0, 1), distance)
    assert data["intersection_count"] == count
    for x, y in data["intersections"]:
        assert y == pytest.approx(distance, abs=1e-9)
        assert x*x + y*y == pytest.approx(4, abs=1e-8)


def test_tangent_limit_is_real_continuous_and_has_no_duplicate_points():
    before = circle_line_geometry((0, 0), 2, (0, 1), 2.000001)
    at = circle_line_geometry((0, 0), 2, (0, 1), 2)
    after = circle_line_geometry((0, 0), 2, (0, 1), 1.999999)
    assert before["intersections"] == []
    assert at["intersections"] == [[0.0, 2.0]]
    assert len(after["intersections"]) == 2
    assert all(math.dist(point, at["intersections"][0]) < .003 for point in after["intersections"])
    assert after["intersections"][0] != after["intersections"][1]


def test_general_line_normal_and_translation_are_supported():
    data = circle_line_geometry((4, -3), 5, (6, 8), 10)
    # Line is 6x+8y=10, distance from (4,-3) is 1.
    assert data["distance"] == pytest.approx(1)
    assert data["intersection_count"] == 2
    for x, y in data["intersections"]:
        assert 6*x+8*y == pytest.approx(10)
        assert (x-4)**2+(y+3)**2 == pytest.approx(25)


@pytest.mark.parametrize("radius,normal,offset", [(0, (0, 1), 2), (2, (0, 0), 2),
                                                     (2, (0, 1), float("nan")),
                                                     (True, (0, 1), 2)])
def test_bad_geometry_cannot_be_rendered(radius, normal, offset):
    with pytest.raises(DemoMathError):
        circle_line_geometry((0, 0), radius, normal, offset)


def test_original_example_independent_lengths_and_foot_identity():
    data = textbook_geometry()
    points = data["points"]
    assert math.dist(points["A"], points["C"]) == pytest.approx(5)
    assert math.dist(points["B"], points["C"]) == pytest.approx(12)
    assert math.dist(points["D"], points["E"]) == pytest.approx(6)
    assert math.dist(points["D"], points["F"]) == pytest.approx(2.5)
    # Check collinearity independently, so swapping feet is not a cosmetic change.
    for foot, start in (("E", "A"), ("F", "B")):
        a, b, q = points[start], points["C"], points[foot]
        assert (q[0]-a[0])*(b[1]-a[1])-(q[1]-a[1])*(b[0]-a[0]) == pytest.approx(0)
    assert data["relations"] == {"AC": "disjoint", "BC": "tangent"}
    assert all(data["checks"].values())


def test_original_example_conditions_are_frozen():
    with pytest.raises(DemoMathError, match="preserve"):
        textbook_geometry({"AC": 12, "BC": 5, "radius": 2.5})
    with pytest.raises(DemoMathError):
        textbook_geometry({"AC": 5, "BC": 12, "radius": 3})


def test_action_order_and_distinct_source_scope_are_enforced():
    plans = default_plans()
    for plan in plans.values():
        assert validate_plan(plan)["duration"] > 0
        assert plan_schema(plan["scene"])["properties"]["scene"]["const"] == plan["scene"]
    changed = copy.deepcopy(plans["circle_line"])
    changed["source"]["example"] = True
    with pytest.raises(DemoMathError, match="distinguished"):
        validate_plan(changed)
    changed = copy.deepcopy(plans["textbook_example"])
    changed["beats"][3], changed["beats"][5] = changed["beats"][5], changed["beats"][3]
    with pytest.raises(DemoMathError, match="order"):
        validate_plan(changed)


def test_no_code_unknown_fields_nan_or_forged_subtitle_timing():
    base = default_plans()["circle_line"]
    changed = copy.deepcopy(base)
    changed["beats"][0]["python"] = "arbitrary()"
    with pytest.raises(DemoMathError, match="unsupported"):
        validate_plan(changed)
    changed = copy.deepcopy(base)
    changed["beats"][0]["duration"] = float("nan")
    with pytest.raises(DemoMathError, match="finite"):
        validate_plan(changed)
    changed = copy.deepcopy(base)
    changed["subtitles"] = [{"start": 4, "end": 5, "text": "原文", "unit_id": "m1"},
                            {"start": 3, "end": 6, "text": "原文2", "unit_id": "m2"}]
    with pytest.raises(DemoMathError, match="ordered"):
        validate_plan(changed)


def test_known_state_revisits_after_initial_demonstration():
    plan = default_plans()["circle_line"]
    plan["beats"].extend({"action": action, "duration": 3}
                         for action in ("set_disjoint", "set_tangent", "set_secant"))
    assert validate_plan(plan)["duration"] == 37
    early = default_plans()["circle_line"]
    early["beats"].insert(1, {"action": "set_tangent", "duration": 2})
    with pytest.raises(DemoMathError, match="only after"):
        validate_plan(early)


def test_editable_summaries_preserve_actual_counts_and_conditions():
    for d, name, count in ((3, "相离", 0), (2, "相切", 1), (1, "相交", 2)):
        svg = circle_summary_svg(circle_line_geometry((0, 0), 2, (0, 1), d))
        ET.fromstring(svg)
        assert f"公共点 {count} 个" in svg and name in svg
    svg = example_summary_svg()
    ET.fromstring(svg)
    assert "DE = BC ÷ 2 = 6" in svg and "DF = AC ÷ 2 = 2.5" in svg
    assert "直线 AC 与圆相离" in svg and "直线 BC 与圆相切" in svg


def test_comparison_has_three_independently_computed_states():
    svg = circle_comparison_svg()
    root = ET.fromstring(svg)
    assert all(f"{count} 个公共点" in svg for count in (0, 1, 2))
    assert all(name in svg for name in ("相离", "相切", "相交"))
    # Three actual circles plus centers; only one + two coral intersections.
    circles = root.findall("{http://www.w3.org/2000/svg}circle")
    assert len([c for c in circles if c.attrib["r"] == "164"]) == 3
    assert len([c for c in circles if c.attrib["fill"] == "#FFAD96"]) == 3


def test_example_svg_marks_only_actual_perpendicular_feet_and_stage_givens():
    ns = {"svg": "http://www.w3.org/2000/svg"}
    early = example_summary_svg(stage="show_circle")
    assert "已知条件" in early and "圆心 D，半径 r = 2.5" in early
    assert "DE = BC ÷ 2 = 6" not in early
    for stage, vertices in (("draw_triangle", {"C"}), ("project_E", {"C", "E"}),
                            ("project_F", {"C", "E", "F"})):
        root = ET.fromstring(example_summary_svg(stage=stage))
        markers = root.findall("svg:polyline", ns)
        assert {p.attrib["data-angle-marker"] for p in markers} == vertices
        # For each actual marker, its two arms must be perpendicular and equal.
        for marker in markers:
            p = [tuple(map(float, pair.split(','))) for pair in marker.attrib["points"].split()]
            u = (p[0][0]-p[1][0], p[0][1]-p[1][1])
            v = (p[2][0]-p[1][0], p[2][1]-p[1][1])
            assert u[0]*v[0]+u[1]*v[1] == pytest.approx(0, abs=2e-5)
            assert math.hypot(*u) == pytest.approx(math.hypot(*v), abs=2e-6)


def test_measured_audio_binding_preserves_samples_source_and_narrated_states(tmp_path):
    rate = 48000
    samples = b"\0\0" * rate * 44
    master = tmp_path / "master.wav"
    with wave.open(str(master), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(samples)
    timeline = [{"unit_id": f"math-{i+1:03d}", "text": "直线与圆。"*9,
                 "start": i*2, "end": (i+1)*2} for i in range(22)]
    plans = bind_source_audio(default_plans(), timeline, master, tmp_path / "bound")
    for name, plan in plans.items():
        count = 15 if name == "circle_line" else 7
        assert validate_plan(plan)["duration"] == pytest.approx(count*2)
        assert max(len(c["text"]) for c in plan["subtitles"]) <= 30
        with wave.open(plan["audio"], "rb") as wav:
            assert wav.getframerate() == rate
            assert wav.getnframes() == count*rate*2
            assert wav.readframes(wav.getnframes()) == samples[:count*rate*4]
    audit = json.loads((tmp_path / "bound/audio-binding-audit.json").read_text(encoding="utf8"))
    assert audit["utterance_timing"] == "measured_PCM_samples"
    assert "approximate" in audit["subtitle_chunk_timing"]
    by_unit = {u["unit_id"]: u for scene in audit["scenes"] for u in scene["units"]}
    assert by_unit["math-006"]["actions"] == ["show_distance"]
    assert by_unit["math-007"]["actions"] == ["move_tangent", "hold_tangent"]
    assert by_unit["math-008"]["actions"] == ["move_secant", "hold_secant", "compare_states"]
    assert by_unit["math-013"]["actions"][0] == "set_disjoint"
    assert by_unit["math-014"]["actions"][0] == "set_tangent"
    assert by_unit["math-015"]["actions"][0] == "set_secant"
    assert by_unit["math-018"]["actions"] == ["project_E", "project_F"]
    assert by_unit["math-019"]["actions"] == ["reason_DE", "reason_DF"]
    assert "".join(c["text"] for p in plans.values() for c in p["subtitles"]) == "".join(c["text"] for c in timeline)


def test_audio_binding_rejects_omitted_units_and_uncovered_tail(tmp_path):
    master = tmp_path / "master.wav"
    with wave.open(str(master), "wb") as wav:
        wav.setparams((1, 2, 100, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0"*4500)
    timeline = [{"unit_id": f"math-{i+1:03d}", "text": "原文", "start": i*2, "end": (i+1)*2} for i in range(22)]
    with pytest.raises(DemoMathError, match="tail"):
        bind_source_audio(default_plans(), timeline, master, tmp_path / "bound")
    with pytest.raises(DemoMathError, match="22 units"):
        bind_source_audio(default_plans(), timeline[:-1], master, tmp_path / "bound")


def test_exploration_is_allowed_only_after_visible_line_and_bound_to_source_observation(tmp_path):
    plan = default_plans()["circle_line"]
    plan["beats"].insert(2, {"action": "explore_sweep", "duration": 8})
    assert validate_plan(plan)["duration"] == 36
    early = default_plans()["circle_line"]
    early["beats"].insert(1, {"action": "explore_sweep", "duration": 8})
    with pytest.raises(DemoMathError, match="already visible line"):
        validate_plan(early)
    short = default_plans()["circle_line"]
    short["beats"].insert(2, {"action": "explore_sweep", "duration": 2})
    with pytest.raises(DemoMathError, match="five seconds"):
        validate_plan(short)
    master = tmp_path / "master.wav"
    with wave.open(str(master), "wb") as wav:
        wav.setparams((1, 2, 100, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0"*13200)
    timeline = [{"unit_id": f"math-{i+1:03d}", "text": "原文", "start": i*6, "end": (i+1)*6} for i in range(22)]
    bound = bind_source_audio(default_plans(), timeline, master, tmp_path / "bound")
    assert validate_plan(bound["circle_line"])["duration"] == pytest.approx(90)
    audit = json.loads((tmp_path / "bound/audio-binding-audit.json").read_text(encoding="utf8"))
    by_unit = {u["unit_id"]: u["actions"] for scene in audit["scenes"] for u in scene["units"]}
    assert by_unit["math-004"] == ["show_line", "explore_sweep"]
    assert by_unit["math-009"] == by_unit["math-011"] == ["explore_sweep"]
    assert by_unit["math-006"] == ["show_distance"]
    assert audit["curated_demo_adaptation"]["authored_action_binding"] is True


def test_exploration_recomputes_continuous_points_and_pauses_at_true_tangency():
    for initial in (1, 3):
        segments = exploration_segments(initial, 10)
        assert sum(s["duration"] for s in segments) == pytest.approx(10)
        assert [s["end"] for s in segments if s["kind"] == "hold" and s["end"] == 2] == [2, 2]
        states = [exploration_geometry_at(initial, 10, i/100) for i in range(1001)]
        assert states[0]["distance"] == pytest.approx(initial)
        assert states[-1]["distance"] == pytest.approx(3)
        assert {s["intersection_count"] for s in states} == {0, 1, 2}
        assert sum(s["intersection_count"] == 1 for s in states) >= 100
        assert max(abs(a["distance"]-b["distance"]) for a,b in zip(states,states[1:])) < .05
        for state in states:
            for x,y in state["intersections"]:
                assert x*x+y*y == pytest.approx(4, abs=1e-8)
                assert y == pytest.approx(state["distance"], abs=1e-9)


def test_source_subtitle_spans_preserve_chinese_words_formulas_and_punctuation():
    text = "如图30.1-2（2），直线与圆只有一个公共点，这时称这条直线与圆相切。若AC=5，BC=12，则比较d>r与d=r。"
    chunks = source_subtitle_spans(text)
    assert "".join(chunks) == text
    assert all(len(c)<=30 for c in chunks)
    assert not any(a.endswith("直") and b.startswith("线") for a,b in zip(chunks,chunks[1:]))
    assert any("AC=5" in c for c in chunks)
    assert any("BC=12" in c for c in chunks)
    assert any("30.1-2" in c for c in chunks)
    assert any(c.endswith(("，","。")) for c in chunks[:-1])


def test_source_subtitle_spans_do_not_silently_truncate_an_unbreakable_token():
    with pytest.raises(DemoMathError, match="indivisible"):
        source_subtitle_spans("A"*31)
