"""将受限分镜渲染为可播放的中文教学 MP4。"""

from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from zhijiang.models import Lesson, LessonSegment, Mode


WIDTH = 1280
HEIGHT = 720
BG = "#081623"
PANEL = "#10283A"
PANEL_LIGHT = "#18364B"
ACCENT = "#6DE2C0"
TEXT = "#F4F7F9"
MUTED = "#A7C0CC"


class VideoError(RuntimeError):
    pass


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["msyhbd.ttc" if bold else "msyh.ttc", "simhei.ttf"]
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise VideoError("未找到中文字体；首版 Windows Demo 需要微软雅黑或黑体。")


def _wrapped(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
             max_width: int, max_lines: int = 3) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text.replace("\n", " "):
        candidate = current + char
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines


def _text_block(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                font: ImageFont.FreeTypeFont, color: str, width: int,
                max_lines: int = 3, spacing: int = 8) -> None:
    x, y = xy
    for line in _wrapped(draw, text, font, width, max_lines):
        draw.text((x, y), line, font=font, fill=color)
        y += font.size + spacing


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
          title: str, body: str, active: bool) -> None:
    fill = PANEL_LIGHT if active else PANEL
    outline = ACCENT if active else "#294B60"
    draw.rounded_rectangle(box, radius=24, fill=fill, outline=outline, width=3 if active else 1)
    x1, y1, x2, _ = box
    _text_block(draw, (x1 + 24, y1 + 24), title, _font(25, True), ACCENT if active else MUTED,
                x2 - x1 - 48, 1)
    _text_block(draw, (x1 + 24, y1 + 76), body, _font(29), TEXT,
                x2 - x1 - 48, 3)


def render_slide(lesson: Lesson, segment: LessonSegment, number: int,
                 reveal: int, total_reveals: int, output: Path) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((44, 34, 1236, 110), radius=20, fill=PANEL)
    draw.text((70, 52), "Lively Class——AI教育Agent", font=_font(33, True), fill=ACCENT)
    label = "确定性演示模式" if lesson.mode == Mode.DEMO else "AI 生成 · 需人工复核"
    draw.text((850, 59), label, font=_font(21), fill=MUTED)
    _text_block(draw, (62, 140), segment.title, _font(42, True), TEXT, 1150, 2)
    draw.text((64, 254), f"{number:02d} / {len(lesson.segments):02d}  ·  {segment.kind.upper()}",
              font=_font(23, True), fill=ACCENT)

    bullets = segment.bullets[:3]
    if segment.kind == "process":
        count = len(bullets)
        card_width = int((1150 - 28 * (count - 1)) / count)
        for index, bullet in enumerate(bullets):
            x = 64 + index * (card_width + 28)
            _card(draw, (x, 306, x + card_width, 526), f"步骤 {index + 1}", bullet,
                  index <= reveal)
            if index < count - 1:
                draw.text((x + card_width + 3, 392), "›", font=_font(42, True), fill=ACCENT)
    elif segment.kind == "formula":
        for index, bullet in enumerate(bullets):
            y = 300 + index * 85
            draw.rounded_rectangle((64, y, 1216, y + 70), radius=18,
                                   fill=PANEL_LIGHT if index <= reveal else PANEL)
            draw.text((88, y + 15), f"{index + 1}.", font=_font(28, True), fill=ACCENT)
            _text_block(draw, (145, y + 16), bullet, _font(27), TEXT, 1010, 1)
    else:
        for index, bullet in enumerate(bullets):
            y = 303 + index * 82
            _card(draw, (64, y, 1216, y + 72), f"要点 {index + 1}", "", index <= reveal)
            _text_block(draw, (247, y + 19), bullet, _font(27), TEXT, 930, 1)

    draw.line((64, 580, 1216, 580), fill="#2D4F62", width=2)
    _text_block(draw, (65, 596), "原文摘录：" + segment.evidence.quote,
                _font(21), MUTED, 1150, 2, 5)
    draw.text((65, 679), f"来源：PDF 第 {segment.evidence.page} 页",
              font=_font(20), fill=ACCENT)
    image.save(output)


def _normalize_audio(source: Path, target: Path, ffmpeg: str) -> float:
    process = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(target)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if process.returncode != 0:
        raise VideoError("配音格式无法转为标准 WAV。")
    with wave.open(str(target), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def render_video(lesson: Lesson, audio_files: list[Path], output: Path) -> None:
    if len(audio_files) != len(lesson.segments):
        raise VideoError("讲稿片段与配音数量不一致。")
    if lesson.deck_plan:
        from zhijiang.page_media import render_shared_video
        render_shared_video(lesson, audio_files, output)
        return
    if any(segment.math_scene or segment.visual_scene for segment in lesson.segments):
        _render_with_math(lesson, audio_files, output)
        return
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    workdir = output.parent
    concatenated = workdir / "narration.wav"
    slides_manifest = workdir / "slides.txt"
    entries: list[tuple[Path, float]] = []
    with wave.open(str(concatenated), "wb") as merged:
        merged.setnchannels(1)
        merged.setsampwidth(2)
        merged.setframerate(24000)
        for index, (segment, audio_file) in enumerate(zip(lesson.segments, audio_files)):
            normalized = workdir / f"audio-normal-{index}.wav"
            duration = _normalize_audio(audio_file, normalized, ffmpeg)
            with wave.open(str(normalized), "rb") as clip:
                merged.writeframes(clip.readframes(clip.getnframes()))
            silence_seconds = 0.28
            merged.writeframes(b"\x00\x00" * math.ceil(24000 * silence_seconds))
            reveals = min(3, max(1, len(segment.bullets)))
            for reveal in range(reveals):
                slide = workdir / f"slide-{index:02d}-{reveal:02d}.png"
                render_slide(lesson, segment, index + 1, reveal, reveals, slide)
                entries.append((slide, (duration + silence_seconds) / reveals))
    if not entries:
        raise VideoError("没有可渲染的课程画面。")
    manifest_lines = []
    for path, duration in entries:
        manifest_lines.append(f"file '{path.as_posix()}'")
        manifest_lines.append(f"duration {duration:.4f}")
    manifest_lines.append(f"file '{entries[-1][0].as_posix()}'")
    slides_manifest.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    process = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(slides_manifest),
            "-i", str(concatenated), "-fps_mode", "vfr",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            "-shortest", str(output),
        ],
        capture_output=True, text=True, check=False,
    )
    if process.returncode != 0 or not output.is_file() or output.stat().st_size < 1000:
        raise VideoError("FFmpeg 视频合成失败。")


def _render_with_math(lesson: Lesson, audio_files: list[Path], output: Path) -> None:
    import tempfile
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="course-clips-", dir=output.parent) as temporary:
        work = Path(temporary)
        clips = []
        for index, (segment, audio) in enumerate(zip(lesson.segments, audio_files)):
            if segment.math_scene or segment.visual_scene:
                clip = output.parent / ("math" if segment.math_scene else "visual") / f"scene-{index+1:02d}" / "clip.mp4"
                if not clip.is_file():
                    raise VideoError("教学场景视频尚未渲染。")
            else:
                part = work / str(index)
                part.mkdir()
                raw = part / "basic.mp4"
                render_video(lesson.model_copy(update={"segments": [segment]}), [audio], raw)
                clip = part / "normalized.mp4"
                result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
                    "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                    "-ac", "2", str(clip)], capture_output=True)
                if result.returncode:
                    raise VideoError("基础片段格式合并失败。")
            clips.append(clip)
        manifest = work / "clips.txt"
        # Source paths are generated job paths, not document filenames.
        manifest.write_text("\n".join(f"file '{clip.resolve().as_posix()}'" for clip in clips), encoding="utf-8")
        # AAC padding can shift concat boundaries by a few milliseconds. Produce
        # a true 30 fps timeline while retaining the original clip audio streams.
        result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(manifest), "-r", "30", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(output)], capture_output=True)
        if result.returncode or not output.is_file():
            raise VideoError("教学视频合并失败。")
