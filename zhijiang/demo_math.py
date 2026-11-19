"""Two deliberately scoped, source-checked Manim demos for the premium showcase.

This is a trusted interpreter, not a Python execution interface for an LLM.
Model output can select only the actions and parameters exposed by ``plan_schema``.
The explanatory circle-line sweep is explicitly separate from textbook Example 1.
No changes are made to the production course generator or its validation rules.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import sys
import wave
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape


INTERPRETER_FILE = Path(__file__).resolve()
INTERPRETER_FILE_SHA256 = hashlib.sha256(INTERPRETER_FILE.read_bytes()).hexdigest()


BG = "#08151F"
PANEL = "#10232F"
INK = "#EDF5F7"
MUTED = "#A5BAC7"
TEAL = "#70E3C3"
BLUE = "#82B8FA"
GOLD = "#F5C879"
CORAL = "#FFAD96"
FONT = "Microsoft YaHei"
REQUIRED_ACTIONS = {
    "circle_line": ["introduce_circle", "show_line", "show_distance", "move_tangent",
                    "hold_tangent", "move_secant", "hold_secant", "compare_states"],
    "textbook_example": ["draw_triangle", "show_midpoint", "show_circle", "project_E",
                         "project_F", "reason_DE", "reason_DF", "conclusion"],
}
REVISIT_ACTIONS = ["set_disjoint", "set_tangent", "set_secant"]
EXPLORATION_ACTIONS = ["explore_sweep"]


class DemoMathError(ValueError):
    """An unexecutable or source-inconsistent showcase plan."""


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DemoMathError(f"{label} must be a finite number")
    return float(value)


def _point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise DemoMathError(f"{label} must contain two coordinates")
    return (_finite(value[0], label), _finite(value[1], label))


def circle_line_geometry(center: tuple[float, float], radius: float,
                         normal: tuple[float, float], offset: float) -> dict:
    """Intersect a circle with n·x=offset, including tangency and no-real-root cases.

    Normalisation makes the result independent of the scale chosen for the line
    equation. The tiny relative tolerance absorbs floating point roundoff only.
    We never take the square root of a genuinely negative discriminant.
    """
    cx, cy = _point(center, "center")
    nx, ny = _point(normal, "normal")
    r = _finite(radius, "radius")
    c = _finite(offset, "offset")
    norm = math.hypot(nx, ny)
    if r <= 0 or norm <= 1e-14:
        raise DemoMathError("radius must be positive and line normal nonzero")
    nx, ny, c = nx / norm, ny / norm, c / norm
    signed = c - nx * cx - ny * cy
    distance = abs(signed)
    foot = (cx + signed * nx, cy + signed * ny)
    tolerance = 1e-10 * max(1.0, r, distance)
    if distance > r + tolerance:
        points, relation = [], "disjoint"
    elif abs(distance - r) <= tolerance:
        points, relation = [list(foot)], "tangent"
    else:
        half_chord = math.sqrt(max(0.0, (r - distance) * (r + distance)))
        tx, ty = -ny, nx
        points = [[foot[0] + sign * half_chord * tx,
                   foot[1] + sign * half_chord * ty] for sign in (-1, 1)]
        relation = "secant"
    return {"center": [cx, cy], "radius": r, "normal": [nx, ny], "offset": c,
            "distance": distance, "foot": list(foot), "intersections": points,
            "intersection_count": len(points), "relation": relation,
            "numeric_tolerance": tolerance}


def projection(point: tuple[float, float], a: tuple[float, float],
               b: tuple[float, float]) -> tuple[float, float]:
    px, py = _point(point, "point")
    ax, ay = _point(a, "line start")
    bx, by = _point(b, "line end")
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    if denominator <= 1e-20:
        raise DemoMathError("projection requires a nondegenerate line")
    t = ((px - ax) * dx + (py - ay) * dy) / denominator
    return (ax + t * dx, ay + t * dy)


def exploration_segments(initial_distance: float, duration: float) -> list[dict]:
    """Trusted continuous 3→2→1→2→3 sweep, with real tangent pauses.

    An optional continuous lead-in returns a previous state to d=3 first.
    The final state is always disjoint, so the following formal definition is
    not accompanied by a leftover secant. These parameters belong only to the
    labelled teaching demonstration, never to textbook Example 1.
    """
    initial = _finite(initial_distance, "initial sweep distance")
    duration = _finite(duration, "sweep duration")
    if not 0 <= initial <= 3 or duration < 5:
        raise DemoMathError("exploration needs d in [0,3] and at least five seconds")
    segments = []
    remaining = duration
    if not math.isclose(initial, 3, abs_tol=1e-10):
        segments.append({"kind": "move", "start": initial, "end": 3.0, "duration": duration*.15})
        remaining *= .85
    current = 3.0
    for kind, target, fraction in (("move",2,.18),("hold",2,.08),("move",1,.18),("hold",1,.06),
                                   ("move",2,.18),("hold",2,.08),("move",3,.18),("hold",3,.06)):
        segments.append({"kind": kind, "start": current, "end": float(target), "duration": remaining*fraction})
        current = float(target)
    # Preserve the exact requested audio duration despite decimal arithmetic.
    segments[-1]["duration"] += duration-sum(s["duration"] for s in segments)
    return segments


def exploration_geometry_at(initial_distance: float, duration: float, elapsed: float) -> dict:
    """Evaluate the same smoothstep trajectory used by the Manim sweep."""
    elapsed = max(0.0, min(duration, _finite(elapsed, "sweep elapsed")))
    passed = 0.0
    segments = exploration_segments(initial_distance, duration)
    distance = 3.0
    for segment in segments:
        if elapsed <= passed+segment["duration"]:
            alpha = (elapsed-passed)/segment["duration"]
            eased = alpha*alpha*(3-2*alpha)
            distance = segment["start"]+(segment["end"]-segment["start"])*eased
            break
        passed += segment["duration"]
    return circle_line_geometry((0,0),2,(0,1),distance)


def textbook_geometry(parameters: dict | None = None) -> dict:
    """Independently compute Example 1; E belongs to AC, F belongs to BC."""
    p = parameters or {"AC": 5, "BC": 12, "radius": 2.5}
    if set(p) != {"AC", "BC", "radius"}:
        raise DemoMathError("Example 1 parameters must be AC, BC, radius")
    ac, bc, r = (_finite(p[k], k) for k in ("AC", "BC", "radius"))
    if not all(math.isclose(actual, expected, abs_tol=1e-12, rel_tol=0)
               for actual, expected in zip((ac, bc, r), (5, 12, 2.5))):
        raise DemoMathError("Example 1 must preserve AC=5, BC=12 and radius=2.5")
    ab = math.hypot(ac, bc)
    # Same orientation as the printed diagram: AB is the horizontal hypotenuse.
    a, b = (0.0, 0.0), (ab, 0.0)
    c = (ac * ac / ab, ac * bc / ab)
    d = (ab / 2, 0.0)
    e, f = projection(d, a, c), projection(d, b, c)
    de, df = math.dist(d, e), math.dist(d, f)
    angle_dot = sum((a[i] - c[i]) * (b[i] - c[i]) for i in range(2))
    # Independent predicates verify lengths, midpoint, feet and their identities.
    checks = {"AC": math.isclose(math.dist(a, c), ac, abs_tol=1e-10),
              "BC": math.isclose(math.dist(b, c), bc, abs_tol=1e-10),
              "AB": math.isclose(math.dist(a, b), 13, abs_tol=1e-10),
              "C_right_angle": abs(angle_dot) < 1e-10,
              "D_midpoint": math.isclose(math.dist(a, d), math.dist(b, d), abs_tol=1e-10),
              "DE_perpendicular_AC": abs(sum((d[i] - e[i]) * (c[i] - a[i]) for i in range(2))) < 1e-10,
              "DF_perpendicular_BC": abs(sum((d[i] - f[i]) * (c[i] - b[i]) for i in range(2))) < 1e-10,
              "DE_equals_6": math.isclose(de, 6, abs_tol=1e-10),
              "DF_equals_2_5": math.isclose(df, 2.5, abs_tol=1e-10)}
    if not all(checks.values()):
        raise DemoMathError("Example 1 failed independent Euclidean checks")
    return {"points": {"A": list(a), "B": list(b), "C": list(c), "D": list(d),
                       "E": list(e), "F": list(f)},
            "lengths": {"AC": ac, "BC": bc, "AB": ab, "DE": de, "DF": df},
            "radius": r, "foot_to_line": {"E": "AC", "F": "BC"},
            "relations": {"AC": "disjoint", "BC": "tangent"}, "checks": checks}


def default_plans() -> dict[str, dict]:
    """Return executable examples, useful as the schema example sent to the Agent."""
    plans = {}
    durations = {"circle_line": [2, 2, 3, 5, 3, 5, 3, 5],
                 "textbook_example": [4, 3, 3, 4, 4, 4, 4, 4]}
    for scene, actions in REQUIRED_ACTIONS.items():
        plans[scene] = {
            "schema_version": 1, "scene": scene,
            "source": {"title": "直线与圆", "printed_pages": [148, 149],
                       "input_id": "math-junior-02-ocr", "example": scene == "textbook_example"},
            "parameters": ({"center": [0, 0], "radius": 2, "start_distance": 3,
                            "end_distance": 1} if scene == "circle_line" else
                           {"AC": 5, "BC": 12, "radius": 2.5}),
            "beats": [{"action": action, "duration": duration}
                      for action, duration in zip(actions, durations[scene])],
            "subtitles": [],
        }
    return plans


def plan_schema(scene: str) -> dict:
    if scene not in REQUIRED_ACTIONS:
        raise DemoMathError("unsupported scene")
    return {"type": "object", "additionalProperties": False,
            "required": ["schema_version", "scene", "source", "parameters", "beats"],
            "properties": {
                "schema_version": {"const": 1}, "scene": {"const": scene},
                "source": {"type": "object", "description": "Original source metadata, not model narration"},
                "parameters": {"const": default_plans()[scene]["parameters"]},
                "beats": {"type": "array", "description": "Each required action exactly once in the given order; optional hold actions may be interleaved.",
                          "items": {"type": "object", "additionalProperties": False,
                                    "required": ["action", "duration"],
                                    "properties": {"action": {"enum": REQUIRED_ACTIONS[scene] + ["hold"] + (REVISIT_ACTIONS + EXPLORATION_ACTIONS if scene == "circle_line" else [])},
                                                   "duration": {"type": "number", "minimum": .25, "maximum": 600}}}},
                "subtitles": {"type": "array", "description": "Program-bound original text and actual audio timestamps; normally attached after planning"},
                "audio": {"type": "string"},
            }}


def validate_plan(plan: dict) -> dict:
    if not isinstance(plan, dict) or set(plan) - {"schema_version", "scene", "source", "parameters", "beats", "subtitles", "audio"}:
        raise DemoMathError("plan contains unsupported fields")
    if plan.get("schema_version") != 1 or plan.get("scene") not in REQUIRED_ACTIONS:
        raise DemoMathError("unsupported plan version or scene")
    scene = plan["scene"]
    source = plan.get("source")
    if not isinstance(source, dict) or source.get("printed_pages") != [148, 149] or not source.get("input_id"):
        raise DemoMathError("source must identify the actual printed pages 148–149")
    if source.get("example") is not (scene == "textbook_example"):
        raise DemoMathError("teaching demonstration and textbook example must be distinguished")
    p = plan.get("parameters")
    if not isinstance(p, dict):
        raise DemoMathError("parameters must be an object")
    if scene == "textbook_example":
        geometry = textbook_geometry(p)
    else:
        if set(p) != {"center", "radius", "start_distance", "end_distance"}:
            raise DemoMathError("unsupported circle-line parameters")
        center = _point(p["center"], "center")
        radius = _finite(p["radius"], "radius")
        start, end = _finite(p["start_distance"], "start_distance"), _finite(p["end_distance"], "end_distance")
        if center != (0.0, 0.0) or radius != 2 or start != 3 or end != 1:
            raise DemoMathError("showcase demonstration parameters are frozen separately from Example 1")
        geometry = {"initial": circle_line_geometry(center, radius, (0, 1), start),
                    "tangent": circle_line_geometry(center, radius, (0, 1), radius),
                    "final": circle_line_geometry(center, radius, (0, 1), end)}
    beats = plan.get("beats")
    if not isinstance(beats, list) or not beats or len(beats) > 100:
        raise DemoMathError("beats must be a nonempty bounded list")
    actions = []
    for beat in beats:
        if not isinstance(beat, dict) or set(beat) != {"action", "duration"}:
            raise DemoMathError("beat contains unsupported fields, including executable code")
        if beat["action"] not in REQUIRED_ACTIONS[scene] + ["hold"] + (REVISIT_ACTIONS + EXPLORATION_ACTIONS if scene == "circle_line" else []):
            raise DemoMathError("unknown action")
        duration = _finite(beat["duration"], "duration")
        if not .25 <= duration <= 600:
            raise DemoMathError("beat duration outside .25–600 seconds")
        if beat["action"] in EXPLORATION_ACTIONS:
            if "show_line" not in actions or duration < 5:
                raise DemoMathError("exploration requires an already visible line and at least five seconds")
        elif beat["action"] in REVISIT_ACTIONS:
            if actions != REQUIRED_ACTIONS[scene]:
                raise DemoMathError("state revisits may occur only after the complete initial demonstration")
        elif beat["action"] != "hold":
            actions.append(beat["action"])
    if actions != REQUIRED_ACTIONS[scene]:
        raise DemoMathError("required actions must appear exactly once in source-compatible order")
    total = sum(beat["duration"] for beat in beats)
    end = 0.0
    for cue in plan.get("subtitles", []):
        if not isinstance(cue, dict) or set(cue) != {"start", "end", "text", "unit_id"}:
            raise DemoMathError("subtitle must bind original unit_id, text and timestamps")
        start, finish = _finite(cue["start"], "subtitle start"), _finite(cue["end"], "subtitle end")
        if start < end - 1e-6 or finish <= start or finish > total + 1e-6:
            raise DemoMathError("subtitles must be ordered, disjoint and inside the audio timeline")
        if not isinstance(cue["text"], str) or not cue["text"] or len(cue["text"]) > 120:
            raise DemoMathError("subtitle text must be a bounded original-text span")
        if not isinstance(cue["unit_id"], str) or not cue["unit_id"]:
            raise DemoMathError("subtitle has no source unit identity")
        end = finish
    audio = plan.get("audio")
    if audio and (not isinstance(audio, str) or not Path(audio).is_file()):
        raise DemoMathError("audio file does not exist")
    return {"scene": scene, "duration": total, "source": source, "geometry": geometry}


def source_subtitle_spans(text: str, max_characters: int = 30) -> list[str]:
    """Preserve every source character, breaking at punctuation or word boundaries.

    Chinese segmentation supplies possible boundaries only; it cannot rewrite
    source text. ASCII formula tokens such as AC=5 and dotted figure numbers
    are protected before segmentation to keep them on one subtitle span.
    """
    import jieba
    if not isinstance(text, str) or not text or not isinstance(max_characters, int) or max_characters < 8:
        raise DemoMathError("subtitle splitting needs original text and a readable character limit")
    protected = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:\s*[=<>]\s*(?:[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?))+|\d+(?:\.\d+)+(?:-\d+(?:\.\d+)*)?|[A-Za-z]+(?: [A-Za-z]+)*")
    atoms, previous = [], 0
    for match in protected.finditer(text):
        atoms.extend(jieba.lcut(text[previous:match.start()], HMM=True))
        atoms.append(match.group())
        previous = match.end()
    atoms.extend(jieba.lcut(text[previous:], HMM=True))
    if "".join(atoms) != text:
        raise DemoMathError("source segmentation changed original characters")
    chunks, buffer = [], []
    punctuation = set("，。；！？：、,.!?;:）)")
    for atom in atoms:
        if len(atom) > max_characters:
            raise DemoMathError("an indivisible source token exceeds the subtitle width; adjust the authored reading input")
        while buffer and len("".join(buffer))+len(atom) > max_characters:
            length, choices = 0, []
            for i, word in enumerate(buffer):
                length += len(word)
                if word[-1:] in punctuation and length >= max_characters*.4:
                    choices.append(i+1)
            split = choices[-1] if choices else len(buffer)
            chunks.append("".join(buffer[:split]))
            buffer = buffer[split:]
        buffer.append(atom)
    if buffer:
        chunks.append("".join(buffer))
    if "".join(chunks) != text or any(not c or len(c)>max_characters for c in chunks):
        raise DemoMathError("subtitle splitting lost characters or exceeded its limit")
    return chunks


def bind_source_audio(plans: dict[str, dict], timeline: list[dict], audio_path: str | Path,
                      output_dir: str | Path) -> dict[str, dict]:
    """Bind the fixed 22-unit source to measured PCM samples and legal actions.

    ``timeline`` consists of {unit_id,text,start,end}; ``end`` includes any pause
    already present in the master WAV. Each original character is preserved.
    Short subtitle spans use proportional timing *within* a measured utterance,
    explicitly recorded as approximate rather than word-forced alignment.
    The two scene WAVs are exact byte slices of the master, with no resampling.
    """
    if set(plans) != set(REQUIRED_ACTIONS):
        raise DemoMathError("audio binding requires both fixed scene plans")
    for plan in plans.values():
        validate_plan(plan)
    if not isinstance(timeline, list) or len(timeline) != 22:
        raise DemoMathError("source audio timeline must contain the actual 22 units")
    audio_path, output_dir = Path(audio_path).resolve(), Path(output_dir).resolve()
    with wave.open(str(audio_path), "rb") as wav:
        params = wav.getparams()
        rate, frame_count = wav.getframerate(), wav.getnframes()
        channels, sample_width = wav.getnchannels(), wav.getsampwidth()
        if wav.getcomptype() != "NONE":
            raise DemoMathError("audio binding requires uncompressed PCM WAV")
        frames = wav.readframes(frame_count)
    rows, previous_end = [], 0
    for index, cue in enumerate(timeline):
        if not isinstance(cue, dict) or set(cue) != {"unit_id", "text", "start", "end"}:
            raise DemoMathError("source timeline needs unit_id, text, start, end only")
        if cue["unit_id"] != f"math-{index+1:03d}" or not isinstance(cue["text"], str) or not cue["text"]:
            raise DemoMathError("source unit identity or original text is missing or out of order")
        start, end = (_finite(cue[k], f"unit {k}") for k in ("start", "end"))
        a, b = round(start*rate), round(end*rate)
        if a != previous_end or b <= a or b > frame_count:
            raise DemoMathError("source timeline must be contiguous, positive and inside the master samples")
        rows.append({**cue, "start_frame": a, "end_frame": b})
        previous_end = b
    if previous_end != frame_count:
        raise DemoMathError("master audio tail is not covered by the source timeline")
    # These bindings follow the frozen source statements, not model narration.
    actions_by_unit = {
        1: ["introduce_circle"], 2: ["hold"], 3: ["hold"], 4: ["show_line"], 5: ["hold"],
        6: ["show_distance"], 7: ["move_tangent", "hold_tangent"],
        8: ["move_secant", "hold_secant", "compare_states"],
        9: ["hold"], 10: ["hold"], 11: ["hold"], 12: ["hold"],
        13: ["set_disjoint"], 14: ["set_tangent"], 15: ["set_secant"],
        16: ["draw_triangle", "show_midpoint", "show_circle"], 17: ["hold"],
        18: ["project_E", "project_F"], 19: ["reason_DE", "reason_DF"],
        20: ["hold"], 21: ["hold"], 22: ["conclusion"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    bound, receipts = {}, []
    for scene, begin, stop in (("circle_line", 0, 15), ("textbook_example", 15, 22)):
        selected = rows[begin:stop]
        first, last = selected[0]["start_frame"], selected[-1]["end_frame"]
        wav_path = output_dir / f"{scene}-source.wav"
        selected_frames = frames[first*channels*sample_width:last*channels*sample_width]
        with wave.open(str(wav_path), "wb") as wav:
            wav.setparams(params._replace(nframes=last-first))
            wav.writeframes(selected_frames)
        plan = copy.deepcopy(plans[scene])
        plan["audio"], plan["beats"], plan["subtitles"] = str(wav_path), [], []
        scene_receipt = {"scene": scene, "source_start_frame": first, "source_end_frame": last,
                         "output_audio": str(wav_path), "units": []}
        for offset, cue in enumerate(selected, begin+1):
            available = (cue["end_frame"]-cue["start_frame"]) / rate
            actions = actions_by_unit[offset]
            if available < .25 * len(actions):
                raise DemoMathError(f"source unit {cue['unit_id']} is too short for its visible actions")
            # Large source spans need a short demonstration followed by a real
            # held state, especially while the source defines tangency/secancy.
            if offset == 4 and available >= 6:
                intro = min(1.2, available*.15)
                actions, durations = ["show_line", "explore_sweep"], [intro, available-intro]
            elif offset in (9, 11) and available >= 5:
                actions, durations = ["explore_sweep"], [available]
            elif offset == 7:
                durations = [min(3.5, max(.25, available*.35)), 0]
                durations[1] = available-durations[0]
            elif offset == 8:
                remaining = available-.75
                durations = [.25+min(3.25, remaining*.4), .25+remaining*.35, 0]
                durations[2] = available-sum(durations[:2])
            elif offset in (13, 14, 15):
                # Translate, then retain the exact named relation while the
                # source finishes the equivalence; no continuously wrong state.
                motion = min(1.2, max(.25, available*.25))
                if available-motion >= .25:
                    actions = actions + ["hold"]
                    durations = [motion, available-motion]
                else:
                    durations = [available]
            else:
                durations = [available/len(actions)] * len(actions)
            plan["beats"].extend({"action": action, "duration": duration}
                                  for action, duration in zip(actions, durations))
            text = cue["text"]
            chunks = source_subtitle_spans(text)
            consumed = 0
            for chunk in chunks:
                start_time = (cue["start_frame"]-first)/rate + available*consumed/len(text)
                consumed += len(chunk)
                end_time = (cue["start_frame"]-first)/rate + available*consumed/len(text)
                plan["subtitles"].append({"start": start_time, "end": end_time,
                                           "text": chunk, "unit_id": cue["unit_id"]})
            scene_receipt["units"].append({"unit_id": cue["unit_id"], "original_text": text,
                                           "source_start_frame": cue["start_frame"], "source_end_frame": cue["end_frame"],
                                           "actions": list(actions), "durations": durations})
        validate_plan(plan)
        if "".join(c["text"] for c in plan["subtitles"]) != "".join(c["text"] for c in selected):
            raise DemoMathError("subtitle binding changed source characters")
        bound[scene] = plan
        receipts.append(scene_receipt)
        (output_dir/f"{scene}-audio-bound.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt = {"master_audio": str(audio_path), "master_sha256": hashlib.sha256(audio_path.read_bytes()).hexdigest(),
               "sample_rate": rate, "channels": channels, "sample_width": sample_width,
               "total_frames": frame_count, "utterance_timing": "measured_PCM_samples",
               "subtitle_chunk_timing": "approximate_within_utterance_proportional_characters",
               "curated_demo_adaptation": {
                   "authored_action_binding": True,
                   "description": "Source units 004, 009 and 011 may use a trusted circle-line exploration sweep when their measured audio time allows it; source narration is unchanged.",
                   "exploration_path": [3,2,1,2,3], "tangent_pauses": True,
                   "intersections": "recomputed from equations at every frame",
                   "insufficient_audio_time": "retain the original legal action/hold"},
               "source_characters_preserved": True, "scenes": receipts}
    (output_dir/"audio-binding-audit.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return bound


def _make_scene_class():
    # Keep optional Manim out of pure geometry/schema imports and unit tests.
    import numpy as np
    from PIL import Image
    from manim import (Scene, Text, Circle, Dot, Line, DashedLine, Polygon, VGroup,
                       RoundedRectangle, Rectangle, DecimalNumber, ValueTracker,
                       FadeIn, FadeOut, Create, Write, Transform, linear, smooth,
                       config)

    class PremiumMathScene(Scene):
        def get_moving_and_static_mobjects(self, animations):
            # Replaced label/container children must not leave stale Cairo families.
            return list(self.mobjects), []

        def construct(self):
            self.data = json.loads(Path(os.environ["LIVELYCLASS_DEMO_MATH_PLAN"]).read_text(encoding="utf-8"))
            self.audit = validate_plan(self.data)
            self.output = Path(os.environ["LIVELYCLASS_DEMO_MATH_OUTPUT"])
            self.output.mkdir(parents=True, exist_ok=True)
            self.camera.background_color = BG
            self.checks, self.samples, self.snapshots = [], [], []
            self.current_subtitle = None
            self.current_subtitle_index = -1
            self.subtitle_layer = VGroup().set_z_index(65)
            self.line_visible = self.distance_visible = False
            self.summary_visible = False
            self.time_tracker = ValueTracker(0)
            self.total_duration = self.audit["duration"]
            self.add(self.time_tracker)
            heading = "直线与圆" if self.data["scene"] == "circle_line" else "教材例 1"
            subtitle = "移动一条直线，观察公共点" if self.data["scene"] == "circle_line" else "同一个圆，两种位置关系"
            self.add(self.label(heading, 38, INK).move_to([-4.9, 3.28, 0]),
                     self.label(subtitle, 22, MUTED).move_to([1.85, 3.28, 0]),
                     Line([-6.55, 2.85, 0], [6.55, 2.85, 0], color="#304554", stroke_width=1.2),
                     self.label("Lively Class", 15, MUTED).move_to([-5.75, -3.73, 0]),
                     self.label("人教数学 九年级上册 · 148—149 页", 15, MUTED).move_to([4.55, -3.73, 0]))
            self.footer_mask = Rectangle(width=14.23, height=.95, fill_color=BG,
                                         fill_opacity=1, stroke_width=0).move_to([0, -3.35, 0]).set_z_index(50)
            self.add(self.footer_mask)
            self.add(self.subtitle_layer)
            # The footer source is redrawn above the subtitle mask.
            self.add(self.label("Lively Class", 15, MUTED).move_to([-5.75, -3.73, 0]).set_z_index(70),
                     self.label("人教数学 九年级上册 · 148—149 页", 15, MUTED).move_to([4.55, -3.73, 0]).set_z_index(70))
            self.progress = Line([-6.55, -3.94, 0], [-6.549, -3.94, 0], color=TEAL, stroke_width=3).set_z_index(80)
            self.progress.add_updater(lambda m: m.put_start_and_end_on(
                [-6.55, -3.94, 0], [-6.55 + 13.1 * min(1, max(0, self.renderer.time / self.total_duration)), -3.94, 0]))
            self.add(self.progress)
            if self.data.get("audio"):
                self.add_sound(str(Path(self.data["audio"]).resolve()))
            if self.data["scene"] == "circle_line":
                self.setup_circle_line()
            else:
                self.setup_example()
            # A frame updater displays text according to actual source-audio cues.
            self.time_tracker.add_updater(lambda m, dt: self.update_subtitles())
            scheduled_end = 0.0
            for index, beat in enumerate(self.data["beats"]):
                scheduled_end += beat["duration"]
                target_frame = round(scheduled_end * config.frame_rate)
                current_frame = round(self.renderer.time * config.frame_rate)
                self.beat_duration = beat["duration"]
                self.available_duration = max(1 / config.frame_rate,
                                              (target_frame-current_frame) / config.frame_rate)
                getattr(self, "action_" + beat["action"])()
                remaining_frames = target_frame - round(self.renderer.time * config.frame_rate)
                if remaining_frames < -1:
                    raise DemoMathError(f"action exceeded actual assigned audio time: {beat['action']}")
                if remaining_frames > 0:
                    # Frame quantisation is cumulative from the actual audio
                    # clock, never a new rounded wait after every short action.
                    self.wait(remaining_frames / config.frame_rate - 1e-9)
                self.capture(index, beat["action"])
            self.update_subtitles()
            self.audit.update({"rendered_seconds": self.renderer.time,
                               "frame_rate": config.frame_rate,
                               "pixel_size": [config.pixel_width, config.pixel_height],
                               "checks": self.checks, "snapshots": self.snapshots,
                               "motion_samples": self.samples,
                               "audio_attached": bool(self.data.get("audio")),
                               "model_output_executed_as_code": False,
                               "scope": "Two preselected demo scenes; not a general textbook benchmark"})
            (self.output / "geometry-audit.json").write_text(json.dumps(self.audit, ensure_ascii=False, indent=2), encoding="utf-8")

        def label(self, text, size=24, color=INK):
            return Text(text, font=FONT, font_size=size, color=color, disable_ligatures=True)

        def update_subtitles(self):
            now = self.renderer.time
            selected = next((i for i, cue in enumerate(self.data.get("subtitles", []))
                             if cue["start"] <= now < cue["end"]), -1)
            if selected == self.current_subtitle_index:
                return
            # Cairo's moving list is captured at animation/wait start. Keep
            # one permanent root in that list and replace only its children;
            # scene-level add/remove can leave old subtitle roots in the frame.
            self.subtitle_layer.remove(*self.subtitle_layer.submobjects)
            self.current_subtitle_index = selected
            self.current_subtitle = None
            if selected >= 0:
                text = self.data["subtitles"][selected]["text"]
                # Never delete text; wrap into natural punctuation or a safe width.
                lines, current = [], ""
                for char in text:
                    current += char
                    if len(current) >= 30:
                        lines.append(current)
                        current = ""
                if current:
                    lines.append(current)
                value = self.label("\n".join(lines), 22, INK).move_to([0, -3.28, 0]).set_z_index(65)
                if value.width > 12.8 or value.height > .75:
                    raise DemoMathError("subtitle span is too large; split the original-text cue without deleting characters")
                self.current_subtitle = value
                self.subtitle_layer.add(value)

        def animate_short(self, *animations, fraction=.6):
            self.play(*animations, run_time=min(1.2, max(.15, self.available_duration * fraction)))

        def action_hold(self):
            pass

        def setup_circle_line(self):
            self.scale = .85
            self.origin = np.array([-2.4, -.35, 0.0])
            self.r = self.data["parameters"]["radius"]
            self.d = ValueTracker(self.data["parameters"]["start_distance"])
            self.add(self.d)
            self.circle = Circle(radius=self.r * self.scale, color=TEAL, stroke_width=4.5,
                                 fill_color=TEAL, fill_opacity=.035).move_to(self.origin)
            self.center_dot = Dot(self.origin, radius=.045, color=TEAL)
            self.center_label = self.label("O", 26, TEAL).move_to(self.origin + [-.22, -.28, 0])
            self.radius = Line(self.origin, self.p(self.r, 0), color=TEAL, stroke_width=3)
            self.radius_label = self.label("r = 2", 24, TEAL).move_to(self.p(1.1, -.35))
            self.circle_group = VGroup(self.circle, self.center_dot, self.center_label, self.radius, self.radius_label)
            self.line = Line(self.p(-4.6, 3), self.p(4.6, 3), color=BLUE, stroke_width=4)
            self.line.add_updater(lambda m: m.put_start_and_end_on(self.p(-4.6, self.d.get_value()), self.p(4.6, self.d.get_value())))
            self.line_label = self.label("l", 28, BLUE)
            self.line_label.add_updater(lambda m: m.move_to(self.p(4.28, self.d.get_value()) + [0, .22, 0]))
            self.foot_dot = Dot(radius=.045, color=GOLD)
            self.foot_dot.add_updater(lambda m: m.move_to(self.p(0, self.d.get_value())))
            self.perp = DashedLine(self.p(0, 0), self.p(0, 3), color=GOLD, stroke_width=3, dash_length=.09)
            self.perp.add_updater(lambda m: m.put_start_and_end_on(self.p(0, 0), self.p(0, self.d.get_value())))
            self.distance_label = self.label("d", 28, GOLD)
            self.distance_label.add_updater(lambda m: m.move_to(self.p(-.34, self.d.get_value() / 2)))
            self.corner = VGroup(Line([0, 0, 0], [.15, 0, 0], color=GOLD, stroke_width=2),
                                 Line([.15, 0, 0], [.15, -.15, 0], color=GOLD, stroke_width=2))
            self.corner.add_updater(lambda m: m.move_to(self.p(0, self.d.get_value()) + [.075, -.075, 0]))
            self.intersections = VGroup(*[Dot(radius=.06, color=CORAL) for _ in range(2)])
            self.intersections.add_updater(self.update_intersections)
            self.panel = RoundedRectangle(width=3.8, height=4.8, corner_radius=.18,
                                          fill_color=PANEL, fill_opacity=1, stroke_color="#2A4354",
                                          stroke_width=1).move_to([4.55, -.03, 0])
            self.badges, self.descriptions, self.relations, self.counts = [], [], [], []
            for title, description, sign, count, color in (
                ("相离", "直线与圆没有公共点", ">", 0, BLUE),
                ("相切", "直线与圆有一个公共点", "=", 1, GOLD),
                ("相交", "直线与圆有两个公共点", "<", 2, CORAL)):
                self.badges.append(self.label(title, 38, color).move_to([4.55, 1.7, 0]))
                self.descriptions.append(self.label(description, 19, MUTED).move_to([4.55, 1.05, 0]))
                self.relations.append(self.label(f"d  {sign}  r", 40, color).move_to([4.55, -.75, 0]))
                self.counts.append(self.label(f"公共点  {count} 个", 25, color).move_to([4.55, -1.55, 0]))
            self.distance_number = DecimalNumber(self.d.get_value(), num_decimal_places=2,
                                                 font_size=32, color=GOLD).move_to([4.95, .35, 0])
            self.distance_number.add_updater(lambda m: m.set_value(self.d.get_value()).move_to([4.95, .35, 0]))
            self.panel_dynamic = VGroup(*self.badges, *self.descriptions, *self.relations, *self.counts)
            self.panel_dynamic.add_updater(self.update_state_labels)
            self.panel_group = VGroup(self.panel, self.panel_dynamic,
                                      self.label("d =", 28, GOLD).move_to([3.85, .35, 0]),
                                      self.distance_number,
                                      self.label("r = 2.00", 24, TEAL).move_to([4.55, -.17, 0]))
            self.add(self.panel_group)
            self.panel_dynamic.update()
            self.add(self.label("位置关系教学示意", 18, MUTED).move_to([-2.45, -2.55, 0]))

        def p(self, x, y):
            return self.origin + np.array([x * self.scale, y * self.scale, 0])

        def current_geometry(self):
            return circle_line_geometry((0, 0), self.r, (0, 1), self.d.get_value())

        def update_intersections(self, group):
            state = self.current_geometry()
            for i, dot in enumerate(group):
                if i < state["intersection_count"]:
                    dot.move_to(self.p(*state["intersections"][i])).set_opacity(1)
                else:
                    dot.set_opacity(0)
            if self.line_visible:
                self.samples.append({"time": round(self.renderer.time, 6), "d": self.d.get_value(),
                                     "r": self.r, "intersection_count": state["intersection_count"],
                                     "intersections": state["intersections"]})

        def update_state_labels(self, group):
            state = self.current_geometry()
            active = {"disjoint": 0, "tangent": 1, "secant": 2}[state["relation"]]
            for family in (self.badges, self.descriptions, self.relations, self.counts):
                for index, item in enumerate(family):
                    item.set_opacity(1 if index == active else 0)

        def action_introduce_circle(self):
            self.animate_short(Create(self.circle), FadeIn(self.center_dot), FadeIn(self.center_label),
                               Create(self.radius), FadeIn(self.radius_label))

        def action_show_line(self):
            self.line_visible = True
            self.animate_short(Create(self.line), FadeIn(self.line_label))
            self.add(self.intersections)

        def action_show_distance(self):
            self.distance_visible = True
            self.animate_short(Create(self.perp), FadeIn(self.foot_dot), FadeIn(self.distance_label), FadeIn(self.corner))

        def move_distance(self, target):
            # Endpoint interpolation is the correct translation for a straight line.
            self.play(self.d.animate.set_value(target), run_time=self.available_duration - 1e-9, rate_func=smooth)
            self.d.set_value(target)
            self.intersections.update()
            self.panel_dynamic.update()

        def action_move_tangent(self):
            self.move_distance(self.r)

        def action_hold_tangent(self):
            if not math.isclose(self.d.get_value(), self.r, abs_tol=1e-10):
                raise DemoMathError("tangent pause does not have d=r")
            self.wait(self.available_duration - 1e-9)

        def action_move_secant(self):
            self.move_distance(self.data["parameters"]["end_distance"])

        def action_hold_secant(self):
            if self.current_geometry()["intersection_count"] != 2:
                raise DemoMathError("secant pause does not have two actual intersections")
            self.wait(self.available_duration - 1e-9)

        def action_set_disjoint(self):
            self.move_distance(self.data["parameters"]["start_distance"])

        def action_set_tangent(self):
            self.move_distance(self.r)

        def action_set_secant(self):
            self.move_distance(self.data["parameters"]["end_distance"])

        def action_explore_sweep(self):
            if not self.line_visible:
                raise DemoMathError("exploration requires the actual line to be visible")
            segments = exploration_segments(self.d.get_value(), self.available_duration)
            first_time = self.renderer.time
            scheduled = 0.0
            for segment in segments:
                scheduled += segment["duration"]
                target_frame = round((first_time+scheduled)*config.frame_rate)
                frames = target_frame-round(self.renderer.time*config.frame_rate)
                if frames <= 0:
                    self.d.set_value(segment["end"])
                    continue
                seconds = frames/config.frame_rate-1e-9
                if segment["kind"] == "move":
                    self.play(self.d.animate.set_value(segment["end"]), run_time=seconds,
                              rate_func=lambda a: a*a*(3-2*a))
                else:
                    self.wait(seconds)
                self.d.set_value(segment["end"])
                self.intersections.update()
                self.panel_dynamic.update()
            if self.current_geometry()["relation"] != "disjoint":
                raise DemoMathError("exploration must finish at the initial disjoint state")

        def action_compare_states(self):
            # A restrained bottom caption consolidates the continuously shown result.
            note = self.label("d > r   相离        d = r   相切        d < r   相交", 22, INK).move_to([-1.85, -2.55, 0])
            self.add(Rectangle(width=8.5, height=.55, fill_color=BG, fill_opacity=1,
                               stroke_width=0).move_to([-2.05, -2.55, 0]))
            self.animate_short(FadeIn(note))
            self.summary_visible = True

        def setup_example(self):
            self.facts = textbook_geometry(self.data["parameters"])
            self.scale = .64
            self.origin = np.array([-6.1, -.85, 0])
            points = self.facts["points"]
            self.points = {name: self.p(*xy) for name, xy in points.items()}
            self.triangle = Polygon(*(self.points[name] for name in "ABC"), color=BLUE,
                                    stroke_width=3.5, fill_color=BLUE, fill_opacity=.035)
            offsets = {"A": [-.15, -.32, 0], "B": [.15, -.32, 0], "C": [-.08, .34, 0],
                       "D": [0, -.33, 0], "E": [-.29, .04, 0], "F": [.27, .17, 0]}
            self.point_marks = {name: VGroup(Dot(point, radius=.04, color=TEAL if name == "D" else INK),
                                           self.label(name, 24, TEAL if name == "D" else INK).move_to(point + offsets[name]))
                                for name, point in self.points.items()}
            self.circle = Circle(radius=2.5 * self.scale, color=TEAL, stroke_width=3.5,
                                 fill_color=TEAL, fill_opacity=.035).move_to(self.points["D"])
            self.DE = Line(self.points["D"], self.points["E"], color=GOLD, stroke_width=3.4)
            self.DF = Line(self.points["D"], self.points["F"], color=CORAL, stroke_width=3.4)
            self.CD = DashedLine(self.points["C"], self.points["D"], color=MUTED,
                                 stroke_width=2, dash_length=.09)
            self.ac_line = Line(self.p(*self.extended_segment("A", "C", -.12)),
                                self.p(*self.extended_segment("A", "C", 1.13)), color=BLUE, stroke_width=2.0)
            self.bc_line = Line(self.p(*self.extended_segment("B", "C", -.08)),
                                self.p(*self.extended_segment("B", "C", 1.12)), color=BLUE, stroke_width=2.0)
            self.conditions = VGroup(
                # E and F are also the leg midpoints. Place length labels on
                # lower half-legs, otherwise they collide with the foot labels.
                self.label("AC = 5", 24, BLUE).move_to(self.mid("A", "E") + [.70, -.05, 0]),
                self.label("BC = 12", 24, BLUE).move_to(self.mid("B", "F") + [.02, .35, 0]))
            self.right_C = self.right_marker("C", "A", "B", .16, BLUE)
            self.right_E = self.right_marker("E", "D", "C", .14, GOLD)
            self.right_F = self.right_marker("F", "D", "B", .14, CORAL)
            self.example_panel = RoundedRectangle(width=3.75, height=4.8, corner_radius=.18,
                                                  fill_color=PANEL, fill_opacity=1,
                                                  stroke_color="#2A4354", stroke_width=1).move_to([4.55, -.03, 0])
            self.panel_content = None
            self.add(self.example_panel, self.label("教材原例 · 半径 2.5", 18, MUTED).move_to([-1.9, -2.65, 0]))
            self.example_text([("已知", INK), ("∠C = 90°", BLUE), ("AC = 5，BC = 12", BLUE)])

        def mid(self, a, b):
            return (self.points[a] + self.points[b]) / 2

        def extended_segment(self, a, b, t):
            aa, bb = self.facts["points"][a], self.facts["points"][b]
            return [aa[i] + t * (bb[i] - aa[i]) for i in range(2)]

        def right_marker(self, vertex, a, b, size, color):
            v, pa, pb = self.points[vertex], self.points[a], self.points[b]
            ua, ub = (pa - v) / np.linalg.norm(pa - v), (pb - v) / np.linalg.norm(pb - v)
            return VGroup(Line(v + size * ua, v + size * (ua + ub), color=color, stroke_width=2),
                          Line(v + size * (ua + ub), v + size * ub, color=color, stroke_width=2))

        def example_text(self, rows):
            if self.panel_content is not None:
                self.remove(self.panel_content)
            self.panel_content = VGroup()
            ys = [1.72, .9, .18, -.55, -1.35, -1.95]
            for i, (text, color) in enumerate(rows):
                value = self.label(text, 31 if i == 0 else 25, color).move_to([4.55, ys[i], 0])
                if value.width > 3.35:
                    raise DemoMathError(f"example panel text overflow: {text}")
                self.panel_content.add(value)
            self.add(self.panel_content)

        def action_draw_triangle(self):
            self.animate_short(Create(self.triangle), *(FadeIn(self.point_marks[name]) for name in "ABC"),
                               FadeIn(self.conditions), Create(self.right_C))

        def action_show_midpoint(self):
            self.animate_short(FadeIn(self.point_marks["D"]))
            self.example_text([("D 是 AB 中点", TEAL), ("AD = DB", INK), ("圆心固定在 D", MUTED)])
            # Equal hypotenuse halves are marked at their actual segment midpoints.
            ticks = VGroup()
            for a, b in (("A", "D"), ("D", "B")):
                pos = self.mid(a, b)
                ticks.add(Line(pos + [0, -.09, 0], pos + [0, .09, 0], color=TEAL, stroke_width=2.5))
            self.add(ticks)

        def action_show_circle(self):
            self.animate_short(Create(self.circle))
            self.example_text([("半径 r = 2.5", TEAL), ("比较两条直线", INK), ("直线 AC 与直线 BC", MUTED)])

        def action_project_E(self):
            self.animate_short(Create(self.DE), FadeIn(self.point_marks["E"]), Create(self.right_E), Create(self.ac_line))
            self.example_text([("先看直线 AC", GOLD), ("DE 垂直 AC", GOLD), ("E 是垂足", INK), ("DE 是圆心到", MUTED), ("直线 AC 的距离", MUTED)])

        def action_reason_DE(self):
            self.add(self.CD)
            self.example_text([("DE = 6", GOLD), ("DE = BC ÷ 2", INK), ("6 > 2.5", GOLD), ("直线 AC 与圆相离", GOLD)])
            value = self.label("DE = 6", 24, GOLD).move_to(self.mid("D", "E") + [-.25, -.27, 0])
            self.animate_short(FadeIn(value))

        def action_project_F(self):
            self.animate_short(Create(self.DF), FadeIn(self.point_marks["F"]), Create(self.right_F), Create(self.bc_line))
            self.example_text([("再看直线 BC", CORAL), ("DF 垂直 BC", CORAL), ("F 是垂足", INK), ("DF 是圆心到", MUTED), ("直线 BC 的距离", MUTED)])

        def action_reason_DF(self):
            self.example_text([("DF = 2.5", CORAL), ("DF = AC ÷ 2", INK), ("2.5 = r", CORAL), ("直线 BC 与圆相切", CORAL)])
            value = self.label("DF = 2.5", 23, CORAL).move_to(self.mid("D", "F") + [.63, -.15, 0])
            self.animate_short(FadeIn(value))

        def action_conclusion(self):
            self.example_text([("距离与位置", INK), ("DE = 6 > 2.5", GOLD), ("直线 AC：相离", GOLD),
                               ("DF = 2.5 = r", CORAL), ("直线 BC：相切", CORAL)])

        def capture(self, index, action):
            self.renderer.update_frame(self)
            image_path = self.output / f"{index + 1:02d}-{action}.png"
            Image.fromarray(self.renderer.get_frame()).save(image_path)
            if self.data["scene"] == "circle_line":
                state = self.current_geometry()
                rendered_distance = float(np.linalg.norm(self.perp.get_end() - self.perp.get_start()) / self.scale)
                rendered_radius = self.circle.width / (2 * self.scale)
                passed = math.isclose(rendered_radius, self.r, abs_tol=1e-7)
                if self.distance_visible:
                    passed = passed and math.isclose(rendered_distance, state["distance"], abs_tol=1e-7)
                if not passed:
                    raise DemoMathError("rendered distance/radius differ from calculated geometry")
                check = {"action": action, "time": self.renderer.time, "state": state,
                         "rendered_distance": rendered_distance, "rendered_radius": rendered_radius, "passed": True}
                svg_path = self.output / f"{index + 1:02d}-{action}.svg"
                svg_path.write_text(circle_comparison_svg() if action == "compare_states" else
                                    circle_summary_svg(state, line_visible=self.line_visible,
                                                       distance_visible=self.distance_visible), encoding="utf-8")
            else:
                actual = {name: ((mark[0].get_center() - self.origin) / self.scale)[:2].tolist()
                          for name, mark in self.point_marks.items()}
                passed = all(np.allclose(actual[name], xy, atol=1e-7)
                             for name, xy in self.facts["points"].items())
                if not passed:
                    raise DemoMathError("rendered textbook point identity or coordinates changed")
                check = {"action": action, "time": self.renderer.time, "rendered_points": actual,
                         "computed_geometry": self.facts, "passed": True}
                svg_path = self.output / f"{index + 1:02d}-{action}.svg"
                svg_path.write_text(example_summary_svg(self.facts, stage=action), encoding="utf-8")
            self.checks.append(check)
            self.snapshots.append({"action": action, "time": self.renderer.time,
                                   "png": image_path.name, "svg": svg_path.name})

    return PremiumMathScene


def _svg_text(x, y, text, size=32, color=INK, anchor="start"):
    return f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif">{escape(text)}</text>'


def circle_summary_svg(state: dict, *, line_visible=True, distance_visible=True) -> str:
    """Editable, self-contained 16:9 summary from the exact same computed state."""
    relation = state["relation"]
    names = {"disjoint": ("相离", ">", BLUE), "tangent": ("相切", "=", GOLD), "secant": ("相交", "<", CORAL)}
    name, sign, color = names[relation]
    cx, cy, unit = 640, 585, 125
    radius, distance = state["radius"], state["distance"]
    pieces = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">',
              f'<title>直线与圆：{name}</title><desc>圆心到直线的距离与半径比较，公共点由方程实际计算。</desc>',
              f'<rect width="1920" height="1080" fill="{BG}"/>',
              _svg_text(80, 105, "直线与圆", 54), _svg_text(80, 155, "位置关系教学示意", 28, MUTED),
              f'<circle cx="{cx}" cy="{cy}" r="{radius*unit}" stroke="{TEAL}" stroke-width="6" fill="{TEAL}" fill-opacity=".035"/>',
              f'<circle cx="{cx}" cy="{cy}" r="7" fill="{TEAL}"/>',
              _svg_text(cx-30,cy+40,"O",36,TEAL),
              f'<line x1="{cx}" y1="{cy}" x2="{cx+radius*unit}" y2="{cy}" stroke="{TEAL}" stroke-width="5"/>',
              _svg_text(cx+radius*unit/2,cy+50,f"r = {radius:g}",34,TEAL,"middle"),
              f'<rect x="1320" y="255" width="500" height="610" rx="24" stroke="#2A4354" fill="{PANEL}"/>',
              _svg_text(1570,355,name,60,color,"middle"),
              _svg_text(1570,475,f"d = {distance:.2f}",42,GOLD,"middle"),
              _svg_text(1570,545,f"r = {radius:.2f}",38,TEAL,"middle"),
              _svg_text(1570,655,f"d {sign} r",60,color,"middle"),
              _svg_text(1570,765,f"公共点 {state['intersection_count']} 个",40,color,"middle")]
    if line_visible:
        y = cy-distance*unit
        pieces.extend([f'<line x1="110" y1="{y}" x2="1230" y2="{y}" stroke="{BLUE}" stroke-width="6"/>',
                       _svg_text(1180,y-20,"l",36,BLUE)])
        if distance_visible:
            pieces.extend([f'<line x1="{cx}" y1="{cy}" x2="{cx}" y2="{y}" stroke="{GOLD}" stroke-width="5" stroke-dasharray="12 9"/>',
                           _svg_text(cx-25,(cy+y)/2,"d",38,GOLD,"end")])
        for x, y in state["intersections"]:
            pieces.append(f'<circle cx="{cx+x*unit}" cy="{cy-y*unit}" r="10" fill="{CORAL}"/>')
    pieces.extend([_svg_text(80,1030,"Lively Class",24,MUTED),
                   _svg_text(1815,1030,"人教数学 九年级上册 148—149 页",24,MUTED,"end"), '</svg>'])
    return "".join(pieces)


def circle_comparison_svg() -> str:
    """Three genuinely computed states, suitable for the summary PPT pages.

    This is a static comparison exported alongside the continuous video. The
    video still shows the same circle and line moving, not a slideshow of states.
    """
    pieces = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">',
              '<title>直线与圆的三种位置关系</title><desc>同半径圆与三条距离不同的直线：相离0个、相切1个、相交2个公共点。所有点由真实方程计算。</desc>',
              f'<rect width="1920" height="1080" fill="{BG}"/>',
              _svg_text(80,105,"直线与圆的三种位置关系",54),
              _svg_text(80,155,"比较圆心到直线的距离 d 与半径 r",28,MUTED)]
    for index, (distance, title, sign, color) in enumerate(
            ((3,"相离",">",BLUE),(2,"相切","=",GOLD),(1,"相交","<",CORAL))):
        state = circle_line_geometry((0, 0), 2, (0, 1), distance)
        left, cx, cy, unit = 80+600*index, 360+600*index, 605, 82
        y = cy-distance*unit
        pieces.extend([f'<rect x="{left}" y="245" width="560" height="685" rx="24" stroke="#2A4354" fill="{PANEL}"/>',
                       _svg_text(cx,310,title,46,color,"middle"),
                       f'<circle cx="{cx}" cy="{cy}" r="164" stroke="{TEAL}" stroke-width="5" fill="{TEAL}" fill-opacity=".035"/>',
                       f'<line x1="{left+35}" y1="{y}" x2="{left+525}" y2="{y}" stroke="{BLUE}" stroke-width="5"/>',
                       f'<line x1="{cx}" y1="{cy}" x2="{cx}" y2="{y}" stroke="{GOLD}" stroke-width="4" stroke-dasharray="10 7"/>',
                       f'<line x1="{cx}" y1="{cy}" x2="{cx+164}" y2="{cy}" stroke="{TEAL}" stroke-width="4"/>',
                       f'<circle cx="{cx}" cy="{cy}" r="5" fill="{TEAL}"/>',
                       _svg_text(cx-18,cy+35,"O",27,TEAL,"end"),
                       _svg_text(cx-18,(cy+y)/2+5,"d",32,GOLD,"end"),
                       _svg_text(cx+92,cy+39,"r",30,TEAL,"middle"),
                       _svg_text(cx,837,f"d {sign} r",45,color,"middle"),
                       _svg_text(cx,891,f"{state['intersection_count']} 个公共点",32,INK,"middle")])
        for x, iy in state["intersections"]:
            pieces.append(f'<circle cx="{cx+x*unit}" cy="{cy-iy*unit}" r="9" fill="{CORAL}"/>')
    pieces.extend([_svg_text(80,1030,"Lively Class",24,MUTED),
                   _svg_text(1815,1030,"位置关系教学示意 · 人教九年级上册 148—149 页",24,MUTED,"end"),'</svg>'])
    return "".join(pieces)


def example_summary_svg(facts: dict | None = None, *, stage: str = "conclusion") -> str:
    facts = facts or textbook_geometry()
    if stage not in REQUIRED_ACTIONS["textbook_example"] + ["hold"]:
        raise DemoMathError("unknown textbook summary stage")
    index = 7 if stage == "hold" else REQUIRED_ACTIONS["textbook_example"].index(stage)
    def point(name):
        x, y = facts["points"][name]
        return (140+82*x, 765-82*y)
    def line(a,b,color,width=5):
        x1,y1=point(a); x2,y2=point(b)
        return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"/>'
    def right_marker(vertex, a, b, color, size=18):
        # Derive both rays from actual Euclidean points after the viewport
        # transformation; the box rotates with the two lines rather than
        # assuming that either leg is horizontal or vertical.
        vx,vy=point(vertex); ax,ay=point(a); bx,by=point(b)
        la,lb=math.hypot(ax-vx,ay-vy),math.hypot(bx-vx,by-vy)
        if min(la,lb)<=1e-9:
            raise DemoMathError("right-angle marker has a degenerate ray")
        ua=((ax-vx)/la,(ay-vy)/la); ub=((bx-vx)/lb,(by-vy)/lb)
        if abs(ua[0]*ub[0]+ua[1]*ub[1])>1e-8:
            raise DemoMathError("right-angle marker rays are not perpendicular")
        pts=[(vx+size*ua[0],vy+size*ua[1]),
             (vx+size*(ua[0]+ub[0]),vy+size*(ua[1]+ub[1])),
             (vx+size*ub[0],vy+size*ub[1])]
        coords=" ".join(f"{x:.6f},{y:.6f}" for x,y in pts)
        return f'<polyline data-angle-marker="{vertex}" points="{coords}" fill="none" stroke="{color}" stroke-width="3"/>'
    dx,dy=point("D")
    pieces=['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">',
            '<title>教材例1：直线AC相离，直线BC相切</title><desc>AC为5，BC为12，C为直角，D为AB中点，圆半径2.5。DE垂直AC为6；DF垂直BC为2.5。</desc>',
            f'<rect width="1920" height="1080" fill="{BG}"/>',_svg_text(80,105,"教材例 1",54),
            _svg_text(80,155,"同一个圆，两种位置关系",28,MUTED),
            *(line(a,b,BLUE) for a,b in (("A","B"),("B","C"),("C","A"))),
            right_marker("C","A","B",BLUE)]
    if index >= 2:
        pieces.append(f'<circle cx="{dx}" cy="{dy}" r="205" stroke="{TEAL}" stroke-width="5" fill="{TEAL}" fill-opacity=".035"/>')
    if index >= 3:
        pieces.append(line("D","E",GOLD))
        pieces.append(right_marker("E","D","C",GOLD))
    if index >= 4:
        pieces.append(line("D","F",CORAL))
        pieces.append(right_marker("F","D","B",CORAL))
    if index >= 5:
        pieces.append(line("C","D",MUTED,3))
    offsets={"A":(-25,40),"B":(10,40),"C":(-20,-25),"D":(-10,40),"E":(-35,0),"F":(20,-15)}
    for name in facts["points"]:
        if name == "D" and index < 1 or name == "E" and index < 3 or name == "F" and index < 4:
            continue
        x,y=point(name);ox,oy=offsets[name]
        pieces.extend([f'<circle cx="{x}" cy="{y}" r="6" fill="{INK}"/>',_svg_text(x+ox,y+oy,name,34)])
    pieces.extend([_svg_text(260,715,"AC = 5",32,BLUE),_svg_text(700,485,"BC = 12",32,BLUE)])
    if index < 5:
        pieces.extend([f'<rect x="1300" y="245" width="520" height="685" rx="24" stroke="#2A4354" fill="{PANEL}"/>',
                       _svg_text(1560,330,"已知条件",42,INK,"middle"),
                       _svg_text(1350,430,"∠C = 90°",34,BLUE),
                       _svg_text(1350,510,"AC = 5，BC = 12",32,BLUE)])
        if index >= 1:
            pieces.append(_svg_text(1350,590,"D 为 AB 的中点",32,TEAL))
        if index >= 2:
            pieces.append(_svg_text(1350,670,"圆心 D，半径 r = 2.5",30,TEAL))
        if index >= 3:
            pieces.append(_svg_text(1350,770,"DE ⟂ AC，垂足为 E",30,GOLD))
        if index >= 4:
            pieces.append(_svg_text(1350,835,"DF ⟂ BC，垂足为 F",30,CORAL))
    if index >= 5:
        pieces.extend([_svg_text(1380,370,"DE = BC ÷ 2 = 6",36,GOLD),
                   _svg_text(1380,445,"6 > 2.5",42,GOLD),_svg_text(1380,515,"直线 AC 与圆相离",32,GOLD),
                      ])
    if index >= 6:
        pieces.extend([_svg_text(1380,650,"DF = AC ÷ 2 = 2.5",36,CORAL),
                       _svg_text(1380,725,"2.5 = r",42,CORAL),_svg_text(1380,795,"直线 BC 与圆相切",32,CORAL)])
    pieces.extend([_svg_text(80,1030,"Lively Class",24,MUTED),
                   _svg_text(1815,1030,"教材原例 · 人教九年级上册 149 页",24,MUTED,"end"),'</svg>'])
    return "".join(pieces)


def render_plan(plan_path: Path, output: Path, *, preview=False) -> dict:
    import manim
    import manimpango
    from manim import tempconfig
    plan_path, output = plan_path.resolve(), output.resolve()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    report = validate_plan(data)
    output.mkdir(parents=True, exist_ok=True)
    os.environ["LIVELYCLASS_DEMO_MATH_PLAN"] = str(plan_path)
    os.environ["LIVELYCLASS_DEMO_MATH_OUTPUT"] = str(output)
    cls = _make_scene_class()
    settings = {"pixel_width": 960 if preview else 1920,
                "pixel_height": 540 if preview else 1080,
                "frame_rate": 15 if preview else 30,
                "frame_width": 128/9, "frame_height": 8,
                "media_dir": str(output / "manim"), "output_file": data["scene"],
                "disable_caching": True, "write_to_movie": True, "save_last_frame": False,
                "verbosity": "WARNING", "background_color": BG}
    with tempconfig(settings):
        instance = cls()
        instance.render()
        movie = Path(instance.renderer.file_writer.movie_file_path)
    import shutil
    final = output / f"{data['scene']}.mp4"
    shutil.copyfile(movie, final)
    font_files = []
    if os.name == "nt":
        for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"):
            path = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/name
            if path.is_file():
                font_files.append({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    current_interpreter_sha = hashlib.sha256(INTERPRETER_FILE.read_bytes()).hexdigest()
    receipt = {"plan": str(plan_path), "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
               "movie": str(final), "movie_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
               "preview": preview, "audio_attached": bool(data.get("audio")),
               "scene": data["scene"], "expected_seconds": report["duration"],
               "audit": str(output / "geometry-audit.json"),
               "actual_interpreter_file": str(INTERPRETER_FILE),
               "actual_interpreter_file_sha256": INTERPRETER_FILE_SHA256,
               "interpreter_file_sha256_at_finish": current_interpreter_sha,
               "interpreter_file_changed_during_process": current_interpreter_sha != INTERPRETER_FILE_SHA256,
               "sys_executable": sys.executable, "python_version": sys.version,
               "manim_version": manim.__version__, "manimpango_version": manimpango.__version__,
               "renderer": "Manim Community Cairo",
               "font": {"requested_family": FONT, "pango_family_available": FONT in manimpango.list_fonts(),
                        "installed_windows_family_files": font_files,
                        "resolution_note": "Pango selects the requested OS family; per-glyph fallback files were not separately traced."}}
    (output / "render-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--write-examples", action="store_true", help="write example plans and schemas instead of rendering")
    args = parser.parse_args()
    if args.write_examples:
        args.output.mkdir(parents=True, exist_ok=True)
        for name, plan in default_plans().items():
            (args.output / f"{name}.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            (args.output / f"{name}.schema.json").write_text(json.dumps(plan_schema(name), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"written": str(args.output.resolve())}))
    elif args.plan:
        print(json.dumps(render_plan(args.plan, args.output, preview=args.preview), ensure_ascii=False))
    else:
        parser.error("--plan or --write-examples is required")


if __name__ == "__main__":
    main()
