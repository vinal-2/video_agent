from pathlib import Path
from typing import List, Tuple, Dict, Any

from faster_whisper import WhisperModel
from moviepy import VideoFileClip

# Lazy global model (loaded once)
_model = None

def get_whisper_model(model_size: str = "small", device: str = "auto", compute_type: str = "int8_float16"):
    """
    Load a faster-whisper model with GPU+CPU fallback.
    device="auto" will use GPU if available, else CPU.
    compute_type="int8_float16" is a good balance for your GPU.
    """
    global _model
    if _model is None:
        _model = WhisperModel(
            model_size,
            device=device,          # "cuda" or "auto"
            compute_type=compute_type
        )
    return _model


def analyze_speech_activity(
    video_path: Path,
    model_size: str = "small",
    device: str = "auto",
    compute_type: str = "int8_float16",
    min_speech_duration: float = 0.5,
) -> Dict[str, Any]:
    """
    Analyze where speech occurs in the audio track of the video.
    Returns:
      {
        "speech_segments": [(start, end), ...],
        "speech_activity_score": float in [0, 1]
      }
    """
    # Extract audio to a temp file
    clip = VideoFileClip(str(video_path))
    audio = clip.audio
    if audio is None:
        clip.close()
        return {
            "speech_segments": [],
            "speech_activity_score": 0.0,
        }

    temp_audio_path = video_path.parent / f"{video_path.stem}_whisper_temp.wav"
    audio.write_audiofile(str(temp_audio_path), logger=None)
    clip.close()

    model = get_whisper_model(model_size=model_size, device=device, compute_type=compute_type)

    # We don't need full text, just timestamps of speech segments.
    segments, info = model.transcribe(
        str(temp_audio_path),
        beam_size=1,
        vad_filter=True,          # use VAD to focus on speech
        word_timestamps=False,
    )

    speech_segments: List[Tuple[float, float]] = []
    total_speech_duration = 0.0

    for seg in segments:
        start = float(seg.start)
        end = float(seg.end)
        dur = end - start
        if dur >= min_speech_duration:
            speech_segments.append((start, end))
            total_speech_duration += dur

    # Compute a simple speech activity score: fraction of audio that has speech
    # relative to total duration.
    # If we can't get duration, just normalize by a rough cap.
    try:
        audio_duration = audio.duration
    except Exception:
        audio_duration = None

    if audio_duration and audio_duration > 0:
        speech_activity_score = max(0.0, min(1.0, total_speech_duration / audio_duration))
    else:
        # Fallback: normalize by a rough max of 60s
        speech_activity_score = max(0.0, min(1.0, total_speech_duration / 60.0))

    # Clean up temp audio
    try:
        temp_audio_path.unlink()
    except Exception:
        pass

    return {
        "speech_segments": speech_segments,
        "speech_activity_score": speech_activity_score,
    }
