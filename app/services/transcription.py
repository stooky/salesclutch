import os
import subprocess
import tempfile
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".webm", ".mp4", ".mpeg", ".mpga", ".oga", ".ogg"}
TEXT_EXTENSIONS = {".txt", ".md"}

# Whisper API limit is 25MB
MAX_FILE_SIZE = 25 * 1024 * 1024


def is_audio_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in AUDIO_EXTENSIONS


def is_text_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in TEXT_EXTENSIONS


def compress_audio(file_path: str) -> str:
    """Compress audio to MP3 if file is too large. Returns path to use."""
    file_size = os.path.getsize(file_path)

    if file_size <= MAX_FILE_SIZE:
        return file_path

    # Create compressed version using ffmpeg
    compressed_path = file_path + ".compressed.mp3"

    try:
        # Convert to mono MP3 at 64kbps - good enough for speech
        subprocess.run([
            "ffmpeg", "-y", "-i", file_path,
            "-vn",  # No video
            "-ac", "1",  # Mono
            "-ar", "16000",  # 16kHz sample rate (fine for speech)
            "-b:a", "64k",  # 64kbps bitrate
            compressed_path
        ], check=True, capture_output=True)

        return compressed_path
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # ffmpeg not available or failed - try without compression
        raise ValueError(
            f"Audio file is too large ({file_size / 1024 / 1024:.1f}MB). "
            f"Maximum size is 25MB. Please compress the file or install ffmpeg on the server."
        )


async def transcribe_audio(file_path: str) -> str:
    """Transcribe audio file using OpenAI Whisper API."""
    # Compress if needed
    actual_path = compress_audio(file_path)
    compressed = actual_path != file_path

    try:
        with open(actual_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )
        return transcript
    finally:
        # Clean up compressed file if we created one
        if compressed and os.path.exists(actual_path):
            os.remove(actual_path)


async def get_transcript(file_path: str, original_filename: str) -> str:
    """Get transcript from either text file or audio file."""
    if is_audio_file(original_filename):
        return await transcribe_audio(file_path)
    elif is_text_file(original_filename):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file type: {original_filename}")
