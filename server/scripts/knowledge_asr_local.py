"""Optional offline ASR adapter: model-dir input-file output-json (no downloads)."""
import json
from pathlib import Path
import sys
import tempfile
import wave


def main():
    from faster_whisper import WhisperModel
    import av
    import numpy as np

    model_dir, source, destination = map(Path, sys.argv[1:4])
    if not model_dir.is_dir() or not (model_dir / "model.bin").is_file():
        raise ValueError("A preinstalled local CTranslate2 Whisper model is required")
    # Reject formats that can reference external URLs. Never infer a decoder from a user extension.
    formats = {"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "mp3", "wav", "aac", "ogg"}
    with tempfile.TemporaryDirectory(prefix="kb-audio-") as temporary:
        audio = Path(temporary) / "audio.wav"
        with av.open(str(source), options={"protocol_whitelist": "file,pipe"}) as container, wave.open(str(audio), "wb") as output:
            if container.format.name not in formats:
                raise ValueError("Unsupported media format")
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000)
            resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
            samples = 0
            if not container.streams.audio:
                raise ValueError("No audio track")
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    values = converted.to_ndarray().astype(np.int16)
                    samples += values.size
                    if samples > 3600 * 16000:
                        raise ValueError("Media exceeds 60 minutes")
                    output.writeframes(values.tobytes())
            for converted in resampler.resample(None):
                values = converted.to_ndarray().astype(np.int16)
                samples += values.size
                if samples > 3600 * 16000:
                    raise ValueError("Media exceeds 60 minutes")
                output.writeframes(values.tobytes())
        model = WhisperModel(str(model_dir.resolve()), device="cpu", compute_type="int8", cpu_threads=2, local_files_only=True)
        segments, _ = model.transcribe(str(audio), beam_size=5, language="zh", vad_filter=True)
        rows = [dict(start=segment.start, end=segment.end, text=segment.text) for segment in segments]
        destination.write_text(json.dumps(dict(segments=rows), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
