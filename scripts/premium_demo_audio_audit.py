"""Independent CPU ASR and native-audio checks for the two premium demos.

Run with the isolated TTS interpreter. This audits actual WAV bytes and actual
submitted TTS text. It never loads a GPU model, rewrites inputs or approves media.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / "data/premium-demo-20261113"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalized_text(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", unicodedata.normalize("NFKC", text)).lower()


def transcript_differences(expected: str, observed: str) -> dict:
    left, right = normalized_text(expected), normalized_text(observed)
    previous = list(range(len(right) + 1))
    for row, char in enumerate(left, 1):
        current = [row]
        for col, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[col] + 1,
                               previous[col - 1] + (char != other)))
        previous = current
    differences = [{"operation": tag, "expected": left[i:j], "observed": right[k:l],
                    "expected_start": i, "observed_start": k}
                   for tag, i, j, k, l in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes()
                   if tag != "equal"]
    flags = []
    if any(item["operation"] == "delete" and len(item["expected"]) >= 6 for item in differences):
        flags.append("possible_omission_or_asr_error")
    if any(item["operation"] == "insert" and len(item["observed"]) >= 6 for item in differences):
        flags.append("possible_extra_speech_or_asr_hallucination")
    # Adjacent repeats are distinguishable from the textbook's repeated relation
    # phrases. Non-adjacent whole-clause additions are handled by insertions above.
    for width in (18, 16, 14, 12, 10, 8, 6):
        for index in range(max(0, len(right) - width * 2 + 1)):
            phrase = right[index:index + width]
            if right[index:index + width * 2] == phrase * 2 and right.count(phrase * 2) > left.count(phrase * 2):
                flags.append("possible_repetition_or_asr_hallucination")
                break
        if "possible_repetition_or_asr_hallucination" in flags:
            break
    for positive, negative in [("大于", "小于"), ("相离", "相交"), ("相切", "相离")]:
        if positive in left and positive not in right and negative in right and negative not in left:
            flags.append(f"comparison_or_relation_needs_review:{positive}->{negative}")
        if negative in left and negative not in right and positive in right and positive not in left:
            flags.append(f"comparison_or_relation_needs_review:{negative}->{positive}")
    edits = previous[-1]
    return {"expected_normalized": left, "asr_normalized": right, "edit_distance": edits,
            "raw_character_error_rate": edits / max(1, len(left)), "differences": differences,
            "transcript_flags": list(dict.fromkeys(flags)),
            "interpretation": "ASR differences include homophones, numeral spelling and recognizer mistakes; not an automatic TTS failure."}


def waveform_metrics(samples: np.ndarray, sample_rate: int) -> dict:
    wave = np.asarray(samples)
    flags = []
    if sample_rate != 48000:
        flags.append("unexpected_sample_rate")
    if wave.ndim != 1:
        flags.append("unexpected_channel_count")
        wave = wave.mean(axis=1) if wave.ndim == 2 else wave.reshape(-1)
    if not len(wave):
        return {"samples": 0, "sample_rate": sample_rate, "waveform_flags": ["empty_audio"]}
    if not np.isfinite(wave).all():
        return {"samples": len(wave), "sample_rate": sample_rate, "waveform_flags": ["non_finite_samples"]}
    rms = float(np.sqrt(np.mean(np.square(wave, dtype=np.float64))))
    peak = float(np.max(np.abs(wave)))
    clipping_ratio = float(np.mean(np.abs(wave) >= 0.9999))
    if clipping_ratio > 0.0001:
        flags.append("possible_clipping")
    if rms < 0.00001:
        flags.append("silent_audio")
    window = max(1, round(sample_rate * 0.02))
    count = (len(wave) + window - 1) // window
    padded = np.pad(wave, (0, count * window - len(wave)))
    windows_rms = np.sqrt(np.mean(padded.reshape(count, window).astype(np.float64) ** 2, axis=1))
    threshold = max(0.0001, rms * 0.02)
    quiet = windows_rms < threshold
    intervals = []
    start = None
    for index, is_quiet in enumerate(np.r_[quiet, False]):
        if is_quiet and start is None:
            start = index
        elif not is_quiet and start is not None:
            end = min(len(wave) / sample_rate, index * window / sample_rate)
            begin = start * window / sample_rate
            duration = end - begin
            if duration >= 0.4:
                boundary = "leading" if start == 0 else "trailing" if index >= count else "interior"
                intervals.append({"start": begin, "end": end, "seconds": duration, "position": boundary})
                if duration >= (1.2 if boundary == "interior" else 1.0):
                    flags.append(f"long_{boundary}_quiet_interval")
            start = None
    return {"sample_rate": sample_rate, "samples": len(wave), "seconds": len(wave) / sample_rate,
            "peak": peak, "rms": rms, "clipping_sample_ratio": clipping_ratio,
            "quiet_window_fraction": float(np.mean(quiet)), "quiet_rms_threshold": threshold,
            "quiet_intervals_at_least_400ms": intervals, "waveform_flags": list(dict.fromkeys(flags)),
            "silence_interpretation": "Signal-level quiet intervals; may be intentional pauses, not automatically missing narration."}


def validate_timeline_unit(unit: dict, receipt: dict, wav_hash: str, stats: dict) -> list[str]:
    errors = []
    request = receipt.get("request", {})
    metadata = request.get("metadata", {})
    if request.get("input") != unit.get("tts_text"):
        errors.append("timeline_differs_from_actual_submitted_tts_text")
    if metadata.get("source_text") != unit.get("text"):
        errors.append("timeline_source_differs_from_request_source")
    if metadata.get("source_unit_id") != unit.get("unit_id"):
        errors.append("source_unit_identity_mismatch")
    if stats.get("samples") != unit.get("samples"):
        errors.append("native_sample_count_differs_from_timeline")
    if stats.get("sample_rate") != 48000:
        errors.append("native_sample_rate_differs_from_timeline")
    if wav_hash != unit.get("unit_wav_sha256") or wav_hash != receipt.get("wav_sha256"):
        errors.append("wav_checksum_differs_from_timeline_or_receipt")
    return errors


def timeline_sample_errors(timeline: dict) -> list[str]:
    errors = []
    position = 0
    rate = timeline["sample_rate"]
    for unit in timeline["units"]:
        if unit["sample_start"] != position or unit["samples"] <= 0 or unit["pause_samples"] < 0:
            errors.append(f"noncontiguous_or_invalid_unit_samples:{unit['unit_id']}")
        expected_start = position / rate
        speech_end = (position + unit["samples"]) / rate
        expected_end = (position + unit["samples"] + unit["pause_samples"]) / rate
        if any(abs(unit[key] - expected) > 0.000001 for key, expected in
               [("start", expected_start), ("speech_end", speech_end), ("end", expected_end)]):
            errors.append(f"timestamp_not_derived_from_native_samples:{unit['unit_id']}")
        position += unit["samples"] + unit["pause_samples"]
    if position != timeline["samples"]:
        errors.append("sum_of_unit_samples_and_pauses_differs_from_master")
    return errors


def compare_master_chunks(audio_dir: Path, timeline: dict) -> tuple[list[dict], list[str]]:
    import soundfile as sf

    records, errors = [], []
    with sf.SoundFile(audio_dir / "master.wav") as master:
        tolerance = (2**-15 if master.subtype == "PCM_16" else 2**-23) + 1e-9
        for unit in timeline["units"]:
            wav = audio_dir / f"{unit['unit_id']}.wav"
            if not wav.exists():
                errors.append(f"missing_unit_in_master_comparison:{unit['unit_id']}")
                continue
            actual, _ = sf.read(wav, dtype="float32")
            master.seek(unit["sample_start"])
            included = master.read(unit["samples"], dtype="float32")
            same_shape = actual.shape == included.shape
            difference = float(np.max(np.abs(actual - included))) if same_shape and len(actual) else None
            master.seek(unit["sample_start"] + unit["samples"])
            pause = master.read(unit["pause_samples"], dtype="float32")
            pause_peak = float(np.max(np.abs(pause))) if len(pause) else 0.0
            if difference is None or difference > tolerance:
                errors.append(f"master_chunk_differs_from_unit_wav:{unit['unit_id']}")
            if pause_peak > tolerance:
                errors.append(f"master_pause_contains_unexpected_audio:{unit['unit_id']}")
            records.append({"unit_id": unit["unit_id"], "maximum_sample_difference": difference,
                            "pause_peak": pause_peak, "comparison_tolerance": tolerance})
    return records, errors


class AudioAuditor:
    def __init__(self, root: Path, threads: int = 2):
        from faster_whisper import WhisperModel

        self.root = root
        self.output = root / "audio-audit"
        self.model_dir = PROJECT / "data/models/whisper-small"
        self.model_sha = sha256(self.model_dir / "model.bin")
        self.model = WhisperModel(str(self.model_dir.resolve()), device="cpu", compute_type="int8",
                                 cpu_threads=threads, num_workers=1, local_files_only=True)

    def audit_case(self, case: str) -> dict:
        import soundfile as sf
        from scipy.signal import resample_poly

        audio_dir = self.root / "audio" / case
        timeline_path = audio_dir / "timeline.json"
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        units = timeline["units"]
        unit_ids = [unit["unit_id"] for unit in units]
        if len(set(unit_ids)) != len(unit_ids):
            raise ValueError("Duplicate timeline unit IDs")
        results = []
        for unit in units:
            unit_id = unit["unit_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", unit_id):
                raise ValueError("Invalid timeline unit ID")
            wav_path = audio_dir / f"{unit_id}.wav"
            receipt_path = audio_dir / f"{unit_id}.receipt.json"
            output = self.output / case / f"{unit_id}.json"
            if not wav_path.exists() or not receipt_path.exists():
                result = {"unit_id": unit_id, "integrity_errors": ["missing_audio_chunk_or_receipt"],
                          "human_listening_completed": False}
                write_json(output, result)
                results.append(result)
                continue
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            expected = receipt["request"]["input"]
            wav_hash = sha256(wav_path)
            expected_sha = hashlib.sha256(expected.encode()).hexdigest()
            if output.exists():
                cached = json.loads(output.read_text(encoding="utf-8"))
                if cached.get("wav_sha256") == wav_hash and cached.get("actual_spoken_text_sha256") == expected_sha:
                    cached["integrity_errors"] = validate_timeline_unit(unit, receipt, wav_hash, cached["waveform"])
                    cached.update(transcript_differences(expected, cached["asr_text"]))
                    write_json(output, cached)
                    results.append(cached)
                    continue
            samples, rate = sf.read(wav_path, dtype="float32", always_2d=False)
            stats = waveform_metrics(samples, rate)
            errors = validate_timeline_unit(unit, receipt, wav_hash, stats)
            if samples.ndim != 1 or rate != 48000:
                raise ValueError(f"Unexpected WAV format for {unit_id}")
            decoded = []
            # ASR gets only waveform samples and a language code, never source text.
            asr_samples = resample_poly(samples, 1, 3).astype("float32")
            segments, info = self.model.transcribe(asr_samples, language="zh", beam_size=5,
                                                  condition_on_previous_text=False, word_timestamps=True,
                                                  vad_filter=False)
            for segment in segments:
                decoded.append({"start": segment.start, "end": segment.end, "text": segment.text,
                                "avg_logprob": segment.avg_logprob,
                                "words": [{"start": word.start, "end": word.end, "word": word.word,
                                           "probability": word.probability} for word in (segment.words or [])]})
            observed = "".join(segment["text"] for segment in decoded)
            result = {"unit_id": unit_id, "wav": str(wav_path.resolve()), "wav_sha256": wav_hash,
                      "source_text": unit["text"], "actual_spoken_text": expected,
                      "actual_spoken_text_sha256": expected_sha, "normalizations": unit.get("normalizations", []),
                      "asr_text": observed, "segments": decoded, "waveform": stats,
                      "integrity_errors": errors, **transcript_differences(expected, observed),
                      "human_listening_completed": False, "automatically_approved": False}
            write_json(output, result)
            results.append(result)
            print(json.dumps({"case": case, "unit": unit_id, "completed": len(results), "total": len(units),
                              "flags": errors + stats["waveform_flags"] + result["transcript_flags"]}), flush=True)
        master_path = audio_dir / "master.wav"
        master_info = sf.info(master_path) if master_path.exists() else None
        master_errors = []
        if master_info is None:
            master_errors.append("missing_master_wav")
        elif master_info.frames != timeline["samples"] or master_info.samplerate != timeline["sample_rate"] or master_info.channels != 1:
            master_errors.append("master_sample_count_rate_or_channels_mismatch")
        master_errors.extend(timeline_sample_errors(timeline))
        master_chunks = []
        if master_info is not None:
            master_chunks, chunk_errors = compare_master_chunks(audio_dir, timeline)
            master_errors.extend(chunk_errors)
        source_join = "\n".join(unit["text"] for unit in units)
        if source_join != timeline["source_text"]:
            master_errors.append("timeline_source_concatenation_mismatch")
        frozen_name = "math-source.json" if case == "math" else "humanities-source.json"
        frozen_path = self.root / "source-preparation" / frozen_name
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        frozen_ids = [unit["unit_id"] for unit in frozen["units"]]
        if unit_ids != frozen_ids or source_join != frozen["source_text"]:
            master_errors.append("timeline_does_not_cover_complete_frozen_source_in_order")
        flags = [{"unit_id": item["unit_id"], "integrity": item.get("integrity_errors", []),
                  "waveform": item.get("waveform", {}).get("waveform_flags", []),
                  "transcript": item.get("transcript_flags", [])} for item in results
                 if item.get("integrity_errors") or item.get("waveform", {}).get("waveform_flags") or item.get("transcript_flags")]
        summary = {"case": case, "timeline": str(timeline_path.resolve()), "timeline_sha256": sha256(timeline_path),
                   "units": len(units), "sample_rate": timeline["sample_rate"], "samples": timeline["samples"],
                   "seconds": timeline["seconds"], "master_integrity_errors": master_errors,
                   "master_chunk_comparisons": master_chunks,
                   "each_unit_in_master_once_in_order": not master_errors,
                   "transcript_review_method": "normalized_differences_and_adjacent_repetition_v2",
                   "frozen_source_path": str(frozen_path.resolve()), "frozen_source_sha256": sha256(frozen_path),
                   "missing_frozen_unit_ids": [unit_id for unit_id in frozen_ids if unit_id not in unit_ids],
                   "unexpected_unit_ids": [unit_id for unit_id in unit_ids if unit_id not in frozen_ids],
                   "asr_model_path": str(self.model_dir.resolve()), "asr_model_sha256": self.model_sha,
                   "asr_device": "cpu", "compute_type": "int8", "reference_prompt_supplied": False,
                   "human_listening_completed": False, "automatic_media_approval": False,
                   "flagged_units": flags, "results": results}
        write_json(self.output / f"{case}-audit.json", summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--cases", nargs="+", choices=["math", "humanities"], default=["math", "humanities"])
    parser.add_argument("--threads", type=int, choices=[2, 3, 4], default=2)
    args = parser.parse_args()
    auditor = AudioAuditor(args.root, args.threads)
    for case in args.cases:
        result = auditor.audit_case(case)
        print(json.dumps({"case_complete": case, "units": result["units"],
                          "flagged_units": len(result["flagged_units"]),
                          "master_integrity_errors": result["master_integrity_errors"],
                          "human_listening_completed": False}), flush=True)


if __name__ == "__main__":
    main()
