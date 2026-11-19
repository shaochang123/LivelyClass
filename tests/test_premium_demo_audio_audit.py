import importlib.util
from pathlib import Path

import numpy as np

SPEC = importlib.util.spec_from_file_location("premium_demo_audio_audit", Path(__file__).parents[1] / "scripts/premium_demo_audio_audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_native_samples_and_signal_clipping_are_measured():
    stats = audit.waveform_metrics(np.full(48000, 0.1), 48000)
    assert stats["samples"] == 48000 and stats["seconds"] == 1
    assert not stats["waveform_flags"]
    clipped = audit.waveform_metrics(np.ones(48000), 48000)
    assert clipped["clipping_sample_ratio"] == 1
    assert "possible_clipping" in clipped["waveform_flags"]


def test_unexpected_long_interior_silence_is_preserved_as_soft_flag():
    audio = np.r_[np.full(24000, 0.1), np.zeros(72000), np.full(24000, 0.1)]
    stats = audit.waveform_metrics(audio, 48000)
    assert "long_interior_quiet_interval" in stats["waveform_flags"]
    assert stats["quiet_intervals_at_least_400ms"][0]["seconds"] == 1.5
    assert "not automatically" in stats["silence_interpretation"]


def test_empty_and_nonfinite_chunks_do_not_pass_as_audio():
    assert audit.waveform_metrics(np.array([]), 48000)["waveform_flags"] == ["empty_audio"]
    assert audit.waveform_metrics(np.array([np.nan]), 48000)["waveform_flags"] == ["non_finite_samples"]


def test_timeline_matches_actual_request_not_old_frozen_tts_spelling():
    unit = {"unit_id": "math-013", "text": "d>r", "tts_text": "d大于阿尔", "samples": 48000,
            "unit_wav_sha256": "same-sha"}
    receipt = {"wav_sha256": "same-sha", "request": {"input": "d大于阿尔",
                "metadata": {"source_text": "d>r", "source_unit_id": "math-013"}}}
    assert audit.validate_timeline_unit(unit, receipt, "same-sha", {"samples": 48000, "sample_rate": 48000}) == []
    receipt["request"]["input"] = "d大于r"
    assert "timeline_differs_from_actual_submitted_tts_text" in audit.validate_timeline_unit(
        unit, receipt, "same-sha", {"samples": 48000, "sample_rate": 48000})


def test_mismatched_wave_frames_and_source_ids_are_rejected():
    unit = {"unit_id": "u1", "text": "原文", "tts_text": "原文", "samples": 48000, "unit_wav_sha256": "first"}
    receipt = {"wav_sha256": "first", "request": {"input": "原文", "metadata": {"source_text": "原文", "source_unit_id": "u2"}}}
    errors = audit.validate_timeline_unit(unit, receipt, "changed", {"samples": 24000, "sample_rate": 48000})
    assert "source_unit_identity_mismatch" in errors
    assert "native_sample_count_differs_from_timeline" in errors
    assert "wav_checksum_differs_from_timeline_or_receipt" in errors


def test_full_clause_omission_is_flagged_without_claiming_tts_failed():
    result = audit.transcript_differences("明月松间照清泉石上流竹喧归浣女", "明月松间照")
    assert "possible_omission_or_asr_error" in result["transcript_flags"]
    assert "not an automatic TTS failure" in result["interpretation"]


def test_homophone_and_number_notation_differences_are_not_omission_flags():
    assert not audit.transcript_differences("中点", "终点")["transcript_flags"]
    assert not audit.transcript_differences("半径二点五", "半径2.5")["transcript_flags"]


def test_newly_repeated_source_phrase_is_flagged():
    result = audit.transcript_differences("明月松间照清泉石上流", "明月松间照清泉石上流明月松间照清泉石上流")
    assert "possible_repetition_or_asr_hallucination" in result["transcript_flags"]


def test_source_relation_phrases_are_not_false_repeat_alarms():
    source = "我们类比点与圆的位置关系的研究进行直线与圆的位置关系的研究"
    observed = "我们类比点圆圆的位置关系的研究进行直线圆圆的位置关系的研究"
    assert "possible_repetition_or_asr_hallucination" not in audit.transcript_differences(source, observed)["transcript_flags"]


def test_native_timeline_requires_contiguous_units_and_real_sample_times():
    timeline = {"sample_rate": 48000, "samples": 7200, "units": [{
        "unit_id": "u1", "sample_start": 0, "samples": 4800, "pause_samples": 2400,
        "start": 0.0, "speech_end": 0.1, "end": 0.15}]}
    assert audit.timeline_sample_errors(timeline) == []
    timeline["units"][0]["sample_start"] = 1
    assert "noncontiguous_or_invalid_unit_samples:u1" in audit.timeline_sample_errors(timeline)
    timeline["units"][0]["sample_start"] = 0
    timeline["units"][0]["speech_end"] = 0.2
    assert "timestamp_not_derived_from_native_samples:u1" in audit.timeline_sample_errors(timeline)
