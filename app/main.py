import json
from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db, init_db
from app.models import Call
from app.config import config
from app.services.storage import save_upload, cleanup_upload
from app.services.transcription import get_transcript
from app.services.processor import process_transcript

app = FastAPI(title="SalesClutch", description="Sales Call Transcript Analyzer")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
async def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    instruction_sets = config.get_all_instruction_sets()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "instruction_sets": instruction_sets}
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload_call(
    request: Request,
    file: UploadFile = File(...),
    instruction_set: str = Form(...),
    db: Session = Depends(get_db)
):
    # Validate instruction set
    inst_set = config.get_instruction_set(instruction_set)
    if not inst_set:
        raise HTTPException(status_code=400, detail="Invalid instruction set")

    if not inst_set.instructions:
        raise HTTPException(status_code=400, detail="Instruction file not found")

    # Save uploaded file
    file_path, original_filename = await save_upload(file)

    try:
        # Get transcript (transcribe if audio)
        transcript = await get_transcript(file_path, original_filename)

        # Process with GPT-4
        result = await process_transcript(transcript, inst_set.instructions)

        # Save to database
        call = Call(
            filename=original_filename,
            transcript=transcript,
            instruction_set=instruction_set,
            summary=result.summary,
            action_items=json.dumps(result.action_items),
            next_step=result.next_step,
            determination=result.determination
        )
        db.add(call)
        db.commit()
        db.refresh(call)

        # Redirect to result page
        return RedirectResponse(url=f"/call/{call.id}", status_code=303)

    finally:
        # Clean up uploaded file
        cleanup_upload(file_path)


@app.get("/call/{call_id}", response_class=HTMLResponse)
async def view_call(request: Request, call_id: int, db: Session = Depends(get_db)):
    call = db.query(Call).filter(Call.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Parse action items from JSON
    action_items = json.loads(call.action_items) if call.action_items else []

    # Get instruction set name
    inst_set = config.get_instruction_set(call.instruction_set)
    instruction_name = inst_set.name if inst_set else call.instruction_set

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "call": call,
            "action_items": action_items,
            "instruction_name": instruction_name
        }
    )


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request, db: Session = Depends(get_db)):
    calls = db.query(Call).order_by(Call.created_at.desc()).all()

    # Add instruction names
    for call in calls:
        inst_set = config.get_instruction_set(call.instruction_set)
        call.instruction_name = inst_set.name if inst_set else call.instruction_set

    return templates.TemplateResponse(
        "history.html",
        {"request": request, "calls": calls}
    )


@app.get("/api/instruction-sets")
async def get_instruction_sets():
    """API endpoint to get all instruction sets."""
    return [
        {
            "id": inst.id,
            "name": inst.name,
            "description": inst.description
        }
        for inst in config.get_all_instruction_sets()
    ]


@app.post("/api/reload-config")
async def reload_config():
    """Reload configuration from YAML file."""
    config.reload()
    return {"status": "ok", "message": "Configuration reloaded"}
