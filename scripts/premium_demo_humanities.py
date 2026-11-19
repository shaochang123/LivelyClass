"""Render the approved still-illustration demo against the measured source audio.

The renderer does not generate pictures, narration or character motion. Every
picture must already exist and be selected. Smooth camera moves and dissolves
are composited locally; the untouched narration master is muxed exactly once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import wave

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/premium-demo-20261113"
WIDTH, HEIGHT, FPS = 1920, 1080, 30
FONT = Path("C:/Windows/Fonts/simsun.ttc")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_inputs(source: dict, story: dict, timeline: dict, selected: dict,
                    master: Path) -> list[dict]:
    """Reject missing media, altered prose, and time/source-order mismatches."""
    units = source["units"]
    ids = [u["unit_id"] for u in units]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Duplicate or empty source units")
    if source.get("unit_separator", "\n").join(u["text"] for u in units) != source["source_text"]:
        raise ValueError("Frozen source ledger does not preserve all text")
    if [s["unit_id"] for s in story["shots"]] != ids:
        raise ValueError("Storyboard order must cover every source unit exactly once")
    measured = timeline["units"]
    if [u["unit_id"] for u in measured] != ids:
        raise ValueError("Timeline order must cover every source unit exactly once")
    if [u["text"] for u in measured] != [u["text"] for u in units]:
        raise ValueError("Audio timeline text differs from frozen source")
    if timeline.get("source_text") != source["source_text"] or timeline.get("complete") is not True:
        raise ValueError("Audio timeline is incomplete or uses another source")
    if timeline.get("sample_rate") != 48000:
        raise ValueError("Expected original 48000 Hz narration")
    with wave.open(str(master), "rb") as wav:
        if wav.getframerate() != 48000 or wav.getnchannels() != 1:
            raise ValueError("Narration master must be 48000 Hz mono PCM")
        actual_samples = wav.getnframes()
    if timeline.get("samples") != actual_samples:
        raise ValueError("Timeline duration does not match actual audio samples")
    if timeline.get("master_sha256") != digest_file(master):
        raise ValueError("Narration master hash differs from the measured source")
    end = 0.0
    result = []
    for unit, shot, timed in zip(units, story["shots"], measured):
        start, speech_end, next_end = [float(timed[k]) for k in ("start", "speech_end", "end")]
        if not all(math.isfinite(v) for v in (start, speech_end, next_end)):
            raise ValueError("Non-finite source time")
        if abs(start - end) > 1 / 48000 or not start < speech_end <= next_end:
            raise ValueError("Source audio units have a gap, overlap or invalid interval")
        image_path = selected.get(unit["unit_id"])
        if not isinstance(image_path, str) or not Path(image_path).is_absolute():
            raise ValueError("Selected images must be explicit absolute file paths")
        image_path = Path(image_path)
        if not image_path.is_file():
            raise ValueError("Selected picture does not exist: " + unit["unit_id"])
        with Image.open(image_path) as im:
            im.verify()
        result.append({**unit, "shot": shot, "timing": timed, "image": image_path})
        end = next_end
    if abs(end - actual_samples / 48000) > 1 / 48000:
        raise ValueError("Timeline end does not cover the complete narration master")
    return result


def split_subtitle(text: str, limit: int = 52) -> list[str]:
    """Split exact source slices; display line wraps are a separate operation."""
    result = []
    while len(text) > limit:
        candidates = [i + 1 for i, ch in enumerate(text[:limit])
                      if ch in "。；！？：，" and i + 1 >= limit // 2]
        split = candidates[-1] if candidates else limit
        result.append(text[:split])
        text = text[split:]
    if text:
        result.append(text)
    return result


def make_cues(rows: list[dict]) -> list[dict]:
    cues = []
    for row in rows:
        timed = row["timing"]
        chunks = split_subtitle(row["text"]) if row["kind"] == "appreciation" else [row["text"]]
        weights = sum(len(c) for c in chunks)
        elapsed = 0
        for index, chunk in enumerate(chunks):
            start = float(timed["start"]) + (float(timed["speech_end"]) - float(timed["start"])) * elapsed / weights
            offset = elapsed
            elapsed += len(chunk)
            end = float(timed["end"]) if index == len(chunks) - 1 else float(timed["start"]) + (float(timed["speech_end"]) - float(timed["start"])) * elapsed / weights
            cues.append({"unit_id": row["unit_id"], "text": chunk, "start": start,
                         "end": end, "character_start": offset, "character_end": elapsed,
                         "timing_method": "measured_unit_interval_proportional_characters" if len(chunks) > 1 else "measured_unit_interval"})
        if "".join(x["text"] for x in cues if x["unit_id"] == row["unit_id"]) != row["text"]:
            raise ValueError("Subtitle cues omitted source characters")
    return cues


def ass_time(seconds: float) -> str:
    centiseconds = round(seconds * 100)
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    whole, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    # Braces and backslashes in source cannot become executable ASS tags.
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", "\\N")


def wrapped(text: str, font_size: int, max_width: int) -> str:
    font = ImageFont.truetype(str(FONT), font_size)
    lines, line = [], ""
    for char in text:
        if line and font.getlength(line + char) > max_width:
            if char in "，。；！？：、）》】”’":
                line += char
                lines.append(line)
                line = ""
            else:
                lines.append(line)
                line = char
        else:
            line += char
    if line:
        lines.append(line)
    if len(lines) > 2:
        raise ValueError("Subtitle would exceed the two-line safe area")
    return "\\N".join(ass_escape(x) for x in lines)


def write_ass(rows: list[dict], cues: list[dict], target: Path) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Subtitle,SimSun,38,&H00F5F4EC,&H00F5F4EC,&H00171813,&H99000000,0,0,0,0,100,100,1,0,1,1.8,1.2,2,140,140,55,1
Style: Poem,SimSun,72,&H00DCE9EB,&H00DCE9EB,&H00372C19,&H99000000,0,0,0,0,100,100,4,0,1,1.2,1.8,7,140,140,130,1
Style: Heading,SimHei,46,&H00DCE9EB,&H00DCE9EB,&H00372C19,&H99000000,0,0,0,0,100,100,3,0,1,1.0,1.5,7,140,140,125,1
Style: Small,SimSun,25,&H00B6CBC4,&H00B6CBC4,&H00372C19,&H99000000,0,0,0,0,100,100,2,0,1,0.8,1,7,140,140,195,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    by_id = {r["unit_id"]: r for r in rows}
    for row in rows:
        start, end = row["timing"]["start"], row["timing"]["end"]
        if row["kind"] == "appreciation":
            title = row.get("heading") or row["shot"]["scene_title"]
            lines.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Heading,,0,0,0,,{{\\fad(350,250)}}{ass_escape(title)}\n")
            words = [k for k in row["shot"].get("keywords", []) if k != title and k != "原创赏析"]
            if words:
                lines.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Small,,0,0,0,,{{\\fad(350,250)}}{ass_escape('  '.join(words))}\n")
    for cue in cues:
        row = by_id[cue["unit_id"]]
        style = "Poem" if row["kind"] == "poem_line" else "Subtitle"
        text = wrapped(cue["text"], 72 if style == "Poem" else 38, 1500)
        if row["kind"] == "title_author":
            text = "{\\an7\\pos(140,145)\\fs78}" + ass_escape(cue["text"])
        lines.append(f"Dialogue: 2,{ass_time(cue['start'])},{ass_time(cue['end'])},{style},,0,0,0,,{{\\fad(180,150)}}{text}\n")
    target.write_text("".join(lines), encoding="utf-8-sig")


def fit_background(source: Path, target: Path) -> None:
    with Image.open(source) as original:
        im = original.convert("RGB")
        ratio = min(WIDTH / im.width, HEIGHT / im.height)
        im = im.resize((round(im.width * ratio), round(im.height * ratio)), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (WIDTH, HEIGHT), (11, 25, 23))
        canvas.paste(im, ((WIDTH - im.width) // 2, (HEIGHT - im.height) // 2))
        canvas.save(target)


def gradient_overlay(target: Path) -> None:
    # Static contrast layer only, independent of the generated art and source text.
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        bottom = max(0.0, (y - 710) / 370) ** 1.6 * 205
        top = max(0.0, (380 - y) / 380) ** 1.6 * 92
        draw.line((0, y, WIDTH, y), fill=(7, 17, 16, round(max(bottom, top))))
    image.save(target)


def ffmpeg(args: list[str], log: Path, *, cwd: Path | None = None) -> None:
    import imageio_ffmpeg
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-y", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError("FFmpeg rendering did not complete; inspect " + str(log))


def render(work: Path, *, output: Path | None = None) -> dict:
    source_path = work / "source-preparation/humanities-source.json"
    source = load(source_path)
    story_path = work / "plans/humanities-story.json"
    timeline_path = work / "audio/humanities/timeline.json"
    master = work / "audio/humanities/master.wav"
    selected_path = work / "selected-presentation-images.json"
    if not selected_path.exists():
        selected_path = work / "selected-images.json"
    timeline = load(timeline_path)
    rows = validate_inputs(source, load(story_path), timeline, load(selected_path), master)
    cues = make_cues(rows)
    folder = work / "humanities-final"
    for name in ["backgrounds", "clips", "keyframes", "logs"]:
        (folder / name).mkdir(parents=True, exist_ok=True)
    target = output or work / "deliverables/humanities/山居秋暝_原诗与赏析.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    save_json(folder / "subtitle-cues.json", {"source_text_sha256": source.get("source_text_sha256"), "cues": cues,
        "within_unit_alignment": "Whole-unit intervals are measured from actual audio sample counts. Multi-sentence appreciation cue boundaries are proportional character estimates, not ASR/forced alignment."})
    write_ass(rows, cues, folder / "subtitles.ass")
    gradient_overlay(folder / "contrast-overlay.png")
    total_frames = math.ceil(timeline["samples"] / 48000 * FPS)
    fade_frames = 14
    durations, clips = [], []
    for index, row in enumerate(rows):
        start_frame = round(float(row["timing"]["start"]) * FPS)
        end_frame = total_frames if index == len(rows) - 1 else round(float(row["timing"]["end"]) * FPS)
        count = end_frame - start_frame
        if count <= fade_frames:
            raise ValueError("Source unit too short for a stable cross-dissolve")
        padded = count + (fade_frames if index < len(rows) - 1 else 0)
        durations.append(count / FPS)
        background = folder / "backgrounds" / (row["unit_id"] + ".png")
        fit_background(row["image"], background)
        clip = folder / "clips" / (row["unit_id"] + ".mp4")
        # Render from a 2x canvas, with a cosine-eased 4.5% push and tiny pan.
        # The previous clip has dissolve handles, keeping global audio unchanged.
        z = f"1+0.045*(0.5-0.5*cos(PI*on/{max(padded-1,1)}))"
        x = f"(iw-iw/zoom)*(0.45+0.08*sin(PI*on/{max(padded-1,1)}))"
        y = "(ih-ih/zoom)*0.46"
        vf = f"scale=3840:2160,zoompan=z='{z}':x='{x}':y='{y}':d={padded}:s={WIDTH}x{HEIGHT}:fps={FPS},setsar=1,format=yuv420p"
        ffmpeg(["-i", str(background.resolve()), "-vf", vf, "-frames:v", str(padded),
                "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-an", str(clip.resolve())],
               folder / "logs" / f"{row['unit_id']}-camera.log")
        clips.append(clip)
    args, graph = [], []
    for clip in clips:
        args += ["-i", str(clip.resolve())]
    previous, elapsed = "0:v", 0.0
    for index in range(1, len(clips)):
        elapsed += durations[index - 1]
        output_label = f"blend{index}"
        graph.append(f"[{previous}][{index}:v]xfade=transition=fade:duration={fade_frames/FPS:.9f}:offset={elapsed:.9f}[{output_label}]")
        previous = output_label
    args += ["-filter_complex_threads", "1", "-filter_complex", ";".join(graph), "-map", f"[{previous}]" if len(clips) > 1 else "0:v",
             "-frames:v", str(total_frames), "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
             str((folder / "background-film.mp4").resolve())]
    ffmpeg(args, folder / "logs/background-transitions.log")
    # Relative filter paths avoid Windows drive-colon escaping. Art stays clean:
    # programmatic text is rendered once here, above the camera/transition layer.
    graph = "[0:v][1:v]overlay=0:0,ass=subtitles.ass:fontsdir='C\\:/Windows/Fonts'[picture]"
    ffmpeg(["-i", "background-film.mp4", "-i", "contrast-overlay.png", "-i", str(master.resolve()),
            "-filter_complex", graph, "-map", "[picture]", "-map", "2:a:0", "-t", f"{timeline['samples']/48000:.9f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
            "-movflags", "+faststart", str(target.resolve())], folder / "logs/final-mux.log", cwd=folder)
    for row in rows:
        moment = (float(row["timing"]["start"]) + float(row["timing"]["speech_end"])) / 2
        ffmpeg(["-i", str(target.resolve()), "-ss", f"{moment:.6f}", "-frames:v", "1", "-update", "1",
                str((folder / "keyframes" / (row["unit_id"] + ".png")).resolve())], folder / "logs" / (row["unit_id"] + "-keyframe.log"))
    ffmpeg(["-v", "error", "-i", str(target.resolve()), "-f", "null", "-"], folder / "logs/complete-decode.log")
    receipt = {"video": str(target.resolve()), "video_sha256": digest_file(target), "source_sha256": digest_file(source_path),
        "story_sha256": digest_file(story_path), "timeline_sha256": digest_file(timeline_path), "selected_images_sha256": digest_file(selected_path),
        "audio_master_sha256": digest_file(master), "audio_uses": 1, "narration_samples": timeline["samples"], "sample_rate": 48000,
        "narration_seconds": timeline["samples"] / 48000, "dimensions": [WIDTH, HEIGHT], "fps": FPS,
        "source_unit_count": len(rows), "subtitle_characters_preserved": True, "complete_decode": True,
        "image_technique": "selected still illustrations, cosine-eased camera moves and cross-dissolves; no generated character video",
        "within_unit_subtitle_timing": "proportional character estimates within measured audio intervals, not forced alignment",
        "full_listening_review": False, "powerpoint_slideshow_review": False,
        "images": [{"unit_id": r["unit_id"], "file": str(r["image"]), "sha256": digest_file(r["image"])} for r in rows]}
    save_json(folder / "final-receipt.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=WORK)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(render(args.work.resolve(), output=args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
