import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from dataclasses import dataclass

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


@dataclass
class ProcessingResult:
    summary: str
    action_items: list[str]
    next_step: str
    determination: str


SYSTEM_PROMPT = """You are a sales call analyst specializing in the Sandler Selling System.
You will analyze call transcripts and provide structured feedback.

You must respond with valid JSON in this exact format:
{
    "summary": "A detailed summary of the call covering key points discussed, prospect responses, and overall flow",
    "action_items": ["Action item 1", "Action item 2", ...],
    "next_step": "The recommended next step - either a specific Sandler step/call type OR 'Close the deal' if appropriate",
    "determination": "Your assessment of deal status: prospect qualification level, likelihood to close, and any red flags or positive indicators"
}"""


async def process_transcript(transcript: str, instructions: str) -> ProcessingResult:
    """Process a call transcript with given instructions using GPT-4."""

    user_prompt = f"""## Instructions
{instructions}

## Call Transcript
{transcript}

Analyze this call according to the instructions above and provide your structured response."""

    response = client.chat.completions.create(
        model="gpt-4-turbo-preview",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        response_format={"type": "json_object"},
        temperature=0.3
    )

    result_text = response.choices[0].message.content
    result_data = json.loads(result_text)

    return ProcessingResult(
        summary=result_data.get("summary", ""),
        action_items=result_data.get("action_items", []),
        next_step=result_data.get("next_step", ""),
        determination=result_data.get("determination", "")
    )
