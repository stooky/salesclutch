import json
import os
from typing import Optional
from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession
from pydantic import BaseModel
from dotenv import load_dotenv

from app.database import get_db, init_db
from app.models import Call, User, Workspace, WorkspaceMember, Deal, DealStage, DealStageChange, DealStageOverride
from app.config import config
from app.services.storage import save_upload, cleanup_upload
from app.services.transcription import get_transcript
from app.services.processor import process_transcript
from app.services.auth import (
    verify_google_token,
    get_or_create_user,
    create_session,
    get_user_from_token,
    delete_session,
    get_user_workspaces,
    get_workspace_if_member,
)

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

app = FastAPI(title="SalesClutch", description="Sales Call Transcript Analyzer")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")


# Pydantic models for API requests
class GoogleAuthRequest(BaseModel):
    credential: str


class CreateDealRequest(BaseModel):
    name: str
    company: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    value: Optional[float] = None
    notes: Optional[str] = None


class SkippedStageExplanation(BaseModel):
    stage: str
    explanation: str


class UpdateDealRequest(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    stage: Optional[str] = None
    value: Optional[float] = None
    notes: Optional[str] = None
    justification: Optional[str] = None  # For manual stage changes
    trigger_call_id: Optional[int] = None  # Call that justifies the stage change
    skipped_stages: Optional[list[SkippedStageExplanation]] = None  # Explanations for skipped stages


# Auth dependency
async def get_current_user(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
) -> Optional[User]:
    """Get current user from session cookie."""
    if not session_token:
        return None
    return get_user_from_token(db, session_token)


async def require_auth(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
) -> User:
    """Require authenticated user, redirect to login if not."""
    user = await get_current_user(request, session_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def check_auto_progression(result, current_stage: str, instruction_set: str) -> Optional[dict]:
    """
    Check if call analysis suggests auto-advancing the deal.
    Returns dict with new_stage and justification if should advance, None otherwise.
    """
    # Stage progression order
    stage_order = [
        DealStage.LEAD.value,
        DealStage.DISCOVERY.value,
        DealStage.DEMO.value,
        DealStage.NEGOTIATION.value,
        DealStage.PROPOSAL.value,
        DealStage.CLOSED_WON.value
    ]

    # Map instruction sets to their corresponding stages
    instruction_to_stage = {
        "bonding_rapport": DealStage.DISCOVERY.value,
        "upfront_contract": DealStage.DISCOVERY.value,
        "pain": DealStage.DEMO.value,
        "budget": DealStage.NEGOTIATION.value,
        "decision": DealStage.NEGOTIATION.value,
        "fulfillment": DealStage.PROPOSAL.value,
        "post_sell": DealStage.CLOSED_WON.value,
    }

    # Check next_step recommendation
    next_step_lower = (result.next_step or "").lower()

    # Parse determination for qualification signals
    determination = result.determination
    if isinstance(determination, str):
        try:
            determination = json.loads(determination)
        except:
            determination = {}

    likelihood = ""
    if isinstance(determination, dict):
        likelihood = determination.get("likelihood_to_close", "").lower()

    # Determine if we should auto-advance
    suggested_stage = instruction_to_stage.get(instruction_set)

    if not suggested_stage:
        return None

    current_idx = stage_order.index(current_stage) if current_stage in stage_order else -1
    suggested_idx = stage_order.index(suggested_stage) if suggested_stage in stage_order else -1

    # Only advance if suggested stage is one step ahead
    if suggested_idx == current_idx + 1:
        # Check for positive signals
        positive_signals = [
            "proceed" in next_step_lower,
            "move forward" in next_step_lower,
            "schedule" in next_step_lower and "demo" in next_step_lower,
            "send proposal" in next_step_lower,
            likelihood in ["high", "very high"],
            "qualified" in (determination.get("prospect_qualification_level", "") if isinstance(determination, dict) else "").lower()
        ]

        if any(positive_signals):
            return {
                "new_stage": suggested_stage,
                "justification": f"Auto-advanced based on {instruction_set.replace('_', ' ')} call analysis. Next step: {result.next_step}"
            }

    return None


@app.on_event("startup")
async def startup():
    init_db()


# ============ Auth Routes ============

@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Login page with Google Sign-In."""
    user = await get_current_user(request, session_token, db)
    if user:
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "google_client_id": GOOGLE_CLIENT_ID}
    )


@app.post("/auth/google")
async def google_auth(
    auth_request: GoogleAuthRequest,
    response: Response,
    db: DBSession = Depends(get_db)
):
    """Handle Google OAuth callback."""
    user_info = verify_google_token(auth_request.credential)
    if not user_info:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    user = get_or_create_user(db, user_info)
    token = create_session(db, user.id)

    # Set session cookie
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60  # 30 days
    )

    return {"status": "ok", "redirect": "/"}


@app.post("/auth/logout")
async def logout(
    response: Response,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Logout and clear session."""
    if session_token:
        delete_session(db, session_token)

    response.delete_cookie("session_token")
    return {"status": "ok", "redirect": "/login"}


@app.get("/auth/logout", response_class=HTMLResponse)
async def logout_get(
    response: Response,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Logout via GET (for link clicks)."""
    if session_token:
        delete_session(db, session_token)

    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session_token")
    return resp


# ============ Main App Routes ============

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    user = await get_current_user(request, session_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Get user's workspaces
    workspaces = get_user_workspaces(db, user.id)

    # Get workspace_id from query param or use first workspace
    workspace_id = request.query_params.get("workspace")
    if workspace_id:
        workspace = get_workspace_if_member(db, int(workspace_id), user.id)
    else:
        workspace = workspaces[0] if workspaces else None

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get deals for linking calls
    deals = db.query(Deal).filter(
        Deal.workspace_id == workspace.id,
        Deal.stage.notin_([DealStage.CLOSED_WON.value, DealStage.CLOSED_LOST.value])
    ).order_by(Deal.name).all()

    instruction_sets = config.get_all_instruction_sets()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "workspaces": workspaces,
            "deals": deals,
            "instruction_sets": instruction_sets
        }
    )


@app.post("/upload", response_class=HTMLResponse)
async def upload_call(
    request: Request,
    file: UploadFile = File(...),
    instruction_set: str = Form(...),
    workspace_id: int = Form(...),
    deal_id: Optional[int] = Form(None),
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    user = await get_current_user(request, session_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Verify workspace access
    workspace = get_workspace_if_member(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

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

        # Determine sequence number if linking to a deal
        sequence_num = None
        if deal_id:
            deal = db.query(Deal).filter(Deal.id == deal_id, Deal.workspace_id == workspace_id).first()
            if deal:
                max_seq = db.query(Call).filter(Call.deal_id == deal_id).count()
                sequence_num = max_seq + 1

        # Save to database
        # Convert determination to JSON string if it's a dict
        determination_str = result.determination
        if isinstance(result.determination, dict):
            determination_str = json.dumps(result.determination)

        call = Call(
            workspace_id=workspace_id,
            deal_id=deal_id,
            filename=original_filename,
            transcript=transcript,
            instruction_set=instruction_set,
            summary=result.summary,
            action_items=json.dumps(result.action_items),
            next_step=result.next_step,
            determination=determination_str,
            sequence_num=sequence_num
        )
        db.add(call)
        db.commit()
        db.refresh(call)

        # Auto-progression: Check if the call analysis suggests advancing the deal
        if deal_id and deal:
            auto_advance = check_auto_progression(result, deal.stage, instruction_set)
            if auto_advance:
                old_stage = deal.stage
                new_stage = auto_advance["new_stage"]
                deal.stage = new_stage

                # Record the auto stage change
                stage_change = DealStageChange(
                    deal_id=deal.id,
                    from_stage=old_stage,
                    to_stage=new_stage,
                    trigger_type="call_analysis",
                    trigger_call_id=call.id,
                    justification=auto_advance["justification"],
                    changed_by=user.id
                )
                db.add(stage_change)
                db.commit()

        # Redirect to result page
        return RedirectResponse(url=f"/call/{call.id}", status_code=303)

    finally:
        # Clean up uploaded file
        cleanup_upload(file_path)


@app.post("/api/upload-and-analyze")
async def api_upload_and_analyze(
    request: Request,
    file: UploadFile = File(...),
    instruction_set: str = Form(...),
    workspace_id: int = Form(...),
    deal_id: Optional[int] = Form(None),
    target_stage: Optional[str] = Form(None),  # The stage user wants to advance to
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """
    Upload and analyze a call, returning JSON result.
    Used by the stage gate modal to show progress and determine if deal advances.
    """
    user = await get_current_user(request, session_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Verify workspace access
    workspace = get_workspace_if_member(db, workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate instruction set
    inst_set = config.get_instruction_set(instruction_set)
    if not inst_set:
        raise HTTPException(status_code=400, detail="Invalid instruction set")

    if not inst_set.instructions:
        raise HTTPException(status_code=400, detail="Instruction file not found")

    # Get the deal if specified
    deal = None
    old_stage = None
    if deal_id:
        deal = db.query(Deal).filter(Deal.id == deal_id, Deal.workspace_id == workspace_id).first()
        if deal:
            old_stage = deal.stage

    # Save uploaded file
    file_path, original_filename = await save_upload(file)

    try:
        # Get transcript (transcribe if audio)
        transcript = await get_transcript(file_path, original_filename)

        # Process with GPT-4
        result = await process_transcript(transcript, inst_set.instructions)

        # Determine sequence number if linking to a deal
        sequence_num = None
        if deal_id and deal:
            max_seq = db.query(Call).filter(Call.deal_id == deal_id).count()
            sequence_num = max_seq + 1

        # Convert determination to JSON string if it's a dict
        determination_str = result.determination
        determination_dict = result.determination
        if isinstance(result.determination, dict):
            determination_str = json.dumps(result.determination)
        elif isinstance(result.determination, str):
            try:
                determination_dict = json.loads(result.determination)
            except:
                determination_dict = {"raw": result.determination}

        # Save call to database
        call = Call(
            workspace_id=workspace_id,
            deal_id=deal_id,
            filename=original_filename,
            transcript=transcript,
            instruction_set=instruction_set,
            summary=result.summary,
            action_items=json.dumps(result.action_items),
            next_step=result.next_step,
            determination=determination_str,
            sequence_num=sequence_num
        )
        db.add(call)
        db.commit()
        db.refresh(call)

        # Check if call qualifies for advancement
        advanced = False
        advancement_blocked = False
        block_reason = None

        if deal_id and deal and target_stage:
            auto_advance = check_auto_progression(result, old_stage, instruction_set)

            if auto_advance:
                # Call analysis supports advancement
                new_stage = target_stage  # Use the target stage user requested
                deal.stage = new_stage
                advanced = True

                # Record the stage change
                stage_change = DealStageChange(
                    deal_id=deal.id,
                    from_stage=old_stage,
                    to_stage=new_stage,
                    trigger_type="call_analysis",
                    trigger_call_id=call.id,
                    justification=f"Advanced based on {instruction_set.replace('_', ' ')} analysis: {result.next_step}",
                    changed_by=user.id
                )
                db.add(stage_change)
                db.commit()
            else:
                # Call analysis does NOT support advancement
                advancement_blocked = True

                # Determine why it was blocked
                reasons = []
                if isinstance(determination_dict, dict):
                    likelihood = determination_dict.get("likelihood_to_close", "").lower()
                    qualification = determination_dict.get("prospect_qualification_level", "").lower()
                    red_flags = determination_dict.get("red_flags", [])

                    if likelihood and likelihood not in ["high", "very high"]:
                        reasons.append(f"Likelihood to close: {likelihood}")
                    if qualification and "not" in qualification.lower():
                        reasons.append(f"Qualification level: {qualification}")
                    if red_flags:
                        reasons.append(f"Red flags identified: {len(red_flags)}")

                if not reasons:
                    reasons.append("Call analysis did not indicate readiness to advance")

                block_reason = "; ".join(reasons)

        return {
            "status": "ok",
            "call_id": call.id,
            "summary": result.summary,
            "next_step": result.next_step,
            "determination": determination_dict,
            "advanced": advanced,
            "advancement_blocked": advancement_blocked,
            "block_reason": block_reason,
            "old_stage": old_stage,
            "new_stage": deal.stage if deal else None,
            "target_stage": target_stage
        }

    finally:
        # Clean up uploaded file
        cleanup_upload(file_path)


@app.get("/call/{call_id}", response_class=HTMLResponse)
async def view_call(
    request: Request,
    call_id: int,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    user = await get_current_user(request, session_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    call = db.query(Call).filter(Call.id == call_id).first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    # Verify workspace access
    workspace = get_workspace_if_member(db, call.workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

    workspaces = get_user_workspaces(db, user.id)

    # Parse action items from JSON
    action_items = json.loads(call.action_items) if call.action_items else []

    # Get instruction set name
    inst_set = config.get_instruction_set(call.instruction_set)
    instruction_name = inst_set.name if inst_set else call.instruction_set

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "workspaces": workspaces,
            "call": call,
            "action_items": action_items,
            "instruction_name": instruction_name
        }
    )


@app.get("/history", response_class=HTMLResponse)
async def history(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    user = await get_current_user(request, session_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    workspaces = get_user_workspaces(db, user.id)

    # Get workspace_id from query param or use first workspace
    workspace_id = request.query_params.get("workspace")
    if workspace_id:
        workspace = get_workspace_if_member(db, int(workspace_id), user.id)
    else:
        workspace = workspaces[0] if workspaces else None

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get calls for this workspace only
    calls = db.query(Call).filter(
        Call.workspace_id == workspace.id
    ).order_by(Call.created_at.desc()).all()

    # Add instruction names
    for call in calls:
        inst_set = config.get_instruction_set(call.instruction_set)
        call.instruction_name = inst_set.name if inst_set else call.instruction_set

    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "workspaces": workspaces,
            "calls": calls
        }
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


# ============ Deal Management Routes ============

@app.get("/deals", response_class=HTMLResponse)
async def deals_kanban(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Kanban board view of all deals."""
    user = await get_current_user(request, session_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    workspaces = get_user_workspaces(db, user.id)

    # Get workspace_id from query param or use first workspace
    workspace_id = request.query_params.get("workspace")
    if workspace_id:
        workspace = get_workspace_if_member(db, int(workspace_id), user.id)
    else:
        workspace = workspaces[0] if workspaces else None

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get all deals for workspace, grouped by stage
    deals = db.query(Deal).filter(Deal.workspace_id == workspace.id).all()

    # Group by stage
    stages = {stage.value: [] for stage in DealStage}
    for deal in deals:
        if deal.stage in stages:
            stages[deal.stage].append(deal)

    instruction_sets = config.get_all_instruction_sets()

    return templates.TemplateResponse(
        "deals_kanban.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "workspaces": workspaces,
            "stages": stages,
            "stage_names": {stage.value: stage.name.replace("_", " ").title() for stage in DealStage},
            "instruction_sets": instruction_sets
        }
    )


@app.get("/deal/{deal_id}", response_class=HTMLResponse)
async def deal_timeline(
    request: Request,
    deal_id: int,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Timeline view of a single deal with all linked calls."""
    user = await get_current_user(request, session_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    # Verify workspace access
    workspace = get_workspace_if_member(db, deal.workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

    workspaces = get_user_workspaces(db, user.id)

    # Get all calls for this deal, ordered by sequence
    calls = db.query(Call).filter(
        Call.deal_id == deal_id
    ).order_by(Call.sequence_num, Call.created_at).all()

    # Add instruction names to calls
    for call in calls:
        inst_set = config.get_instruction_set(call.instruction_set)
        call.instruction_name = inst_set.name if inst_set else call.instruction_set

    # Get stage history (progression log)
    stage_history = db.query(DealStageChange).filter(
        DealStageChange.deal_id == deal_id
    ).order_by(DealStageChange.changed_at.desc()).all()

    # Get stage overrides
    stage_overrides = db.query(DealStageOverride).filter(
        DealStageOverride.deal_id == deal_id
    ).order_by(DealStageOverride.created_at.desc()).all()

    instruction_sets = config.get_all_instruction_sets()

    return templates.TemplateResponse(
        "deal_timeline.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "workspaces": workspaces,
            "deal": deal,
            "calls": calls,
            "stage_history": stage_history,
            "stage_overrides": stage_overrides,
            "instruction_sets": instruction_sets,
            "stage_names": {stage.value: stage.name.replace("_", " ").title() for stage in DealStage}
        }
    )


@app.post("/api/deals")
async def create_deal(
    deal_request: CreateDealRequest,
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Create a new deal."""
    user = await get_current_user(request, session_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Get workspace from query param
    workspace_id = request.query_params.get("workspace")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="Workspace ID required")

    workspace = get_workspace_if_member(db, int(workspace_id), user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

    deal = Deal(
        workspace_id=workspace.id,
        name=deal_request.name,
        company=deal_request.company,
        contact_name=deal_request.contact_name,
        contact_email=deal_request.contact_email,
        value=deal_request.value,
        notes=deal_request.notes,
        stage=DealStage.LEAD.value
    )
    db.add(deal)
    db.commit()
    db.refresh(deal)

    return {"status": "ok", "deal_id": deal.id}


@app.patch("/api/deals/{deal_id}")
async def update_deal(
    deal_id: int,
    deal_request: UpdateDealRequest,
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Update a deal (including stage changes for Kanban drag-drop)."""
    from datetime import datetime

    user = await get_current_user(request, session_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    workspace = get_workspace_if_member(db, deal.workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

    old_stage = deal.stage

    # Update fields if provided
    if deal_request.name is not None:
        deal.name = deal_request.name
    if deal_request.company is not None:
        deal.company = deal_request.company
    if deal_request.contact_name is not None:
        deal.contact_name = deal_request.contact_name
    if deal_request.contact_email is not None:
        deal.contact_email = deal_request.contact_email
    if deal_request.stage is not None and deal_request.stage != old_stage:
        deal.stage = deal_request.stage
        # Set closed_at if moving to closed stage
        if deal_request.stage in [DealStage.CLOSED_WON.value, DealStage.CLOSED_LOST.value]:
            deal.closed_at = datetime.utcnow()

        # Determine trigger type based on whether stages were skipped
        trigger_type = "manual"
        if deal_request.skipped_stages and len(deal_request.skipped_stages) > 0:
            trigger_type = "override"

        # Record the stage change in progression log
        stage_change = DealStageChange(
            deal_id=deal.id,
            from_stage=old_stage,
            to_stage=deal_request.stage,
            trigger_type=trigger_type,
            trigger_call_id=deal_request.trigger_call_id,
            justification=deal_request.justification or f"Manually moved from {old_stage} to {deal_request.stage}",
            changed_by=user.id
        )
        db.add(stage_change)
        db.flush()  # Get the stage_change.id

        # Record any skipped stage overrides
        if deal_request.skipped_stages:
            for skipped in deal_request.skipped_stages:
                override = DealStageOverride(
                    deal_id=deal.id,
                    stage_change_id=stage_change.id,
                    skipped_stage=skipped.stage,
                    explanation=skipped.explanation,
                    overridden_by=user.id
                )
                db.add(override)

    if deal_request.value is not None:
        deal.value = deal_request.value
    if deal_request.notes is not None:
        deal.notes = deal_request.notes

    db.commit()
    return {"status": "ok"}


@app.delete("/api/deals/{deal_id}")
async def delete_deal(
    deal_id: int,
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Delete a deal."""
    user = await get_current_user(request, session_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    workspace = get_workspace_if_member(db, deal.workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

    # Unlink calls from this deal (don't delete them)
    db.query(Call).filter(Call.deal_id == deal_id).update({"deal_id": None, "sequence_num": None})

    db.delete(deal)
    db.commit()
    return {"status": "ok"}


@app.get("/api/deals/{deal_id}/calls")
async def get_deal_calls(
    deal_id: int,
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Get all calls for a deal."""
    user = await get_current_user(request, session_token, db)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    deal = db.query(Deal).filter(Deal.id == deal_id).first()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    workspace = get_workspace_if_member(db, deal.workspace_id, user.id)
    if not workspace:
        raise HTTPException(status_code=403, detail="Access denied")

    calls = db.query(Call).filter(Call.deal_id == deal_id).order_by(Call.sequence_num).all()
    return [call.to_dict() for call in calls]


# ============ Workspace Management ============

@app.get("/workspace/settings", response_class=HTMLResponse)
async def workspace_settings(
    request: Request,
    session_token: Optional[str] = Cookie(None),
    db: DBSession = Depends(get_db)
):
    """Workspace settings page."""
    user = await get_current_user(request, session_token, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    workspaces = get_user_workspaces(db, user.id)

    workspace_id = request.query_params.get("workspace")
    if workspace_id:
        workspace = get_workspace_if_member(db, int(workspace_id), user.id)
    else:
        workspace = workspaces[0] if workspaces else None

    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Get workspace members
    members = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == workspace.id
    ).all()

    # Load user info for each member
    for member in members:
        member.user_info = db.query(User).filter(User.id == member.user_id).first()

    return templates.TemplateResponse(
        "workspace_settings.html",
        {
            "request": request,
            "user": user,
            "workspace": workspace,
            "workspaces": workspaces,
            "members": members
        }
    )
