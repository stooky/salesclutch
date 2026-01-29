import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from app.models import User, Session as DBSession, Workspace, WorkspaceMember, WorkspaceRole

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
SESSION_EXPIRY_DAYS = 30


def verify_google_token(token: str) -> Optional[dict]:
    """Verify Google OAuth token and return user info."""
    try:
        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )

        if idinfo["iss"] not in ["accounts.google.com", "https://accounts.google.com"]:
            return None

        return {
            "google_id": idinfo["sub"],
            "email": idinfo["email"],
            "name": idinfo.get("name", idinfo["email"].split("@")[0]),
            "avatar_url": idinfo.get("picture")
        }
    except Exception as e:
        print(f"Token verification failed: {e}")
        return None


def get_or_create_user(db: Session, user_info: dict) -> User:
    """Get existing user or create new one from Google auth."""
    user = db.query(User).filter(User.google_id == user_info["google_id"]).first()

    if user:
        # Update user info in case it changed
        user.name = user_info["name"]
        user.avatar_url = user_info.get("avatar_url")
        db.commit()
        return user

    # Create new user
    user = User(
        email=user_info["email"],
        name=user_info["name"],
        avatar_url=user_info.get("avatar_url"),
        google_id=user_info["google_id"]
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Create default workspace for new user
    workspace = Workspace(
        name=f"{user_info['name']}'s Workspace",
        created_by=user.id
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    # Add user as owner of their workspace
    membership = WorkspaceMember(
        user_id=user.id,
        workspace_id=workspace.id,
        role=WorkspaceRole.OWNER.value
    )
    db.add(membership)
    db.commit()

    return user


def create_session(db: Session, user_id: int) -> str:
    """Create a new session for user and return token."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=SESSION_EXPIRY_DAYS)

    session = DBSession(
        user_id=user_id,
        token=token,
        expires_at=expires_at
    )
    db.add(session)
    db.commit()

    return token


def get_user_from_token(db: Session, token: str) -> Optional[User]:
    """Get user from session token."""
    session = db.query(DBSession).filter(
        DBSession.token == token,
        DBSession.expires_at > datetime.utcnow()
    ).first()

    if not session:
        return None

    return db.query(User).filter(User.id == session.user_id).first()


def delete_session(db: Session, token: str):
    """Delete a session (logout)."""
    db.query(DBSession).filter(DBSession.token == token).delete()
    db.commit()


def cleanup_expired_sessions(db: Session):
    """Remove expired sessions."""
    db.query(DBSession).filter(DBSession.expires_at < datetime.utcnow()).delete()
    db.commit()


def get_user_workspaces(db: Session, user_id: int) -> list[Workspace]:
    """Get all workspaces user is a member of."""
    memberships = db.query(WorkspaceMember).filter(
        WorkspaceMember.user_id == user_id
    ).all()

    workspace_ids = [m.workspace_id for m in memberships]
    return db.query(Workspace).filter(Workspace.id.in_(workspace_ids)).all()


def get_workspace_if_member(db: Session, workspace_id: int, user_id: int) -> Optional[Workspace]:
    """Get workspace if user is a member, otherwise None."""
    membership = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id
    ).first()

    if not membership:
        return None

    return db.query(Workspace).filter(Workspace.id == workspace_id).first()
