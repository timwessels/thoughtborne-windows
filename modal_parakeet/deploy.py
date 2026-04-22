"""
Modal deployment for parakeet-primeline German STT model.

Parakeet-primeline: 600M parameter model, 4.11% WER on Tuda-De (spontaneous German).
Based on NVIDIA's parakeet-tdt-0.6b-v3, fine-tuned for German by Florian Zimmermeister.

Setup:
    pip install modal
    modal setup          # Browser-Authentifizierung
    modal deploy modal_parakeet/deploy.py

After deployment, the endpoint URL is printed. Add it to .env as MODAL_ENDPOINT_URL.

Test:
    python modal_parakeet/test_endpoint.py test_audio.mp3
"""

import modal
from fastapi import Request

app = modal.App("parakeet-german")

# Container image with NeMo framework and dependencies
# The model is pre-downloaded during image build (cached in image layer)
nemo_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libsndfile1", "ffmpeg")
    .pip_install(
        "Cython",
        "nemo_toolkit[asr]",
        "lhotse>=1.32.2",  # Override NeMo's pin (1.31.1) - fixes PyTorch 2.10 compat
        "huggingface_hub",
        "soundfile",
        "librosa",
        "fastapi[standard]",
    )
    # Pre-download model into image layer (avoids download on every container start)
    .run_commands(
        "python3 -c \""
        "from huggingface_hub import hf_hub_download; "
        "hf_hub_download(repo_id='primeline/parakeet-primeline', filename='2_95_WER.nemo')"
        "\""
    )
)


@app.cls(
    gpu="T4",
    image=nemo_image,
    enable_memory_snapshot=True,   # 2-5s cold start instead of 20s
    scaledown_window=300,           # 5 min - keeps container warm between requests
    timeout=600,                   # Max 10 min per request
)
class ParakeetGerman:
    """Parakeet-primeline German STT model served via Modal."""

    @modal.enter()
    def load_model(self):
        """Load the NeMo ASR model into GPU memory."""
        from huggingface_hub import hf_hub_download
        from nemo.collections.asr.models import ASRModel

        model_path = hf_hub_download(
            repo_id="primeline/parakeet-primeline",
            filename="2_95_WER.nemo",
        )
        self.model = ASRModel.restore_from(model_path, map_location="cuda")
        self.model.eval()
        print("Parakeet-primeline model loaded successfully")

    @modal.method()
    def transcribe_audio(self, audio_bytes: bytes) -> dict:
        """Transcribe audio bytes to text. Handles resampling to 16kHz."""
        import tempfile
        import os
        import time as time_mod
        import traceback
        import soundfile as sf
        import librosa

        start = time_mod.time()

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
            f.write(audio_bytes)
            input_path = f.name

        wav_path = input_path + "_16k.wav"

        try:
            # Load audio and resample to 16kHz mono (required by Parakeet)
            print(f"Loading audio from {input_path} ({len(audio_bytes)} bytes)")
            audio, _ = librosa.load(input_path, sr=16000, mono=True)
            sf.write(wav_path, audio, 16000)
            print(f"Resampled to 16kHz: {len(audio)} samples ({len(audio)/16000:.1f}s)")

            # Transcribe
            print("Starting transcription...")
            output = self.model.transcribe([wav_path])
            print(f"Transcription output type: {type(output)}")
            print(f"Output[0] type: {type(output[0])}")
            print(f"Output[0] repr: {repr(output[0])[:200]}")

            # Extract text from NeMo output
            if hasattr(output[0], 'text'):
                text = output[0].text
            elif isinstance(output[0], str):
                text = output[0]
            else:
                text = str(output[0])

            elapsed = time_mod.time() - start
            print(f"Transcription done in {elapsed:.2f}s: {text[:100]}")

            return {
                "text": text.strip(),
                "processing_time": round(elapsed, 2),
            }

        except Exception as e:
            elapsed = time_mod.time() - start
            tb = traceback.format_exc()
            print(f"ERROR in transcribe_audio: {e}\n{tb}")
            return {
                "error": str(e),
                "traceback": tb,
                "text": "",
                "processing_time": round(elapsed, 2),
            }

        finally:
            for p in [input_path, wav_path]:
                try:
                    os.remove(p)
                except OSError:
                    pass


# Thin HTTP endpoint that receives audio and forwards to GPU class
@app.function(image=modal.Image.debian_slim().pip_install("fastapi[standard]"))
@modal.fastapi_endpoint(method="POST", label="parakeet-german")
async def transcribe(request: Request):
    """HTTP endpoint for audio transcription.

    Accepts raw audio bytes (MP3, WAV, FLAC, etc.) as POST body.
    Returns JSON: {"text": "...", "processing_time": 1.23}
    """
    audio_bytes = await request.body()

    if not audio_bytes or len(audio_bytes) < 100:
        return {"error": "Audio data too small or empty", "text": ""}

    model = ParakeetGerman()
    result = model.transcribe_audio.remote(audio_bytes)
    return result
