import os
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".webm", ".mp4", ".mpeg", ".mpga", ".oga", ".ogg"}
TEXT_EXTENSIONS = {".txt", ".md"}


def is_audio_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in AUDIO_EXTENSIONS


def is_text_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in TEXT_EXTENSIONS


async def transcribe_audio(file_path: str) -> str:
    """Transcribe audio file using OpenAI Whisper API."""
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="text"
        )
    return transcript


async def get_transcript(file_path: str, original_filename: str) -> str:
    """Get transcript from either text file or audio file."""
    if is_audio_file(original_filename):
        return await transcribe_audio(file_path)
    elif is_text_file(original_filename):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        raise ValueError(f"Unsupported file type: {original_filename}")
