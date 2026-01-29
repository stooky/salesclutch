from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, Numeric, Boolean, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum


class DealStage(str, enum.Enum):
    LEAD = "lead"
    DISCOVERY = "discovery"
    DEMO = "demo"
    NEGOTIATION = "negotiation"
    PROPOSAL = "proposal"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


class WorkspaceRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    avatar_url = Column(String(500))
    google_id = Column(String(255), unique=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    workspace_memberships = relationship("WorkspaceMember", back_populates="user")
    sessions = relationship("Session", back_populates="user")


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    members = relationship("WorkspaceMember", back_populates="workspace")
    deals = relationship("Deal", back_populates="workspace")
    calls = relationship("Call", back_populates="workspace")
    invites = relationship("WorkspaceInvite", back_populates="workspace")


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    role = Column(String(20), default=WorkspaceRole.MEMBER.value)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="workspace_memberships")
    workspace = relationship("Workspace", back_populates="members")

    __table_args__ = (
        Index("ix_workspace_member", "workspace_id", "user_id", unique=True),
    )


class WorkspaceInvite(Base):
    __tablename__ = "workspace_invites"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    email = Column(String(255), nullable=False)
    invited_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    workspace = relationship("Workspace", back_populates="invites")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="sessions")


class Deal(Base):
    __tablename__ = "deals"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    company = Column(String(255))
    contact_name = Column(String(255))
    contact_email = Column(String(255))
    stage = Column(String(20), default=DealStage.LEAD.value, index=True)
    value = Column(Numeric(12, 2))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime(timezone=True))

    # Relationships
    workspace = relationship("Workspace", back_populates="deals")
    calls = relationship("Call", back_populates="deal", order_by="Call.sequence_num")
    stage_history = relationship("DealStageChange", back_populates="deal", order_by="DealStageChange.changed_at.desc()")
    stage_overrides = relationship("DealStageOverride", back_populates="deal", order_by="DealStageOverride.created_at.desc()")

    __table_args__ = (
        Index("ix_deal_workspace_stage", "workspace_id", "stage"),
    )


class DealStageChange(Base):
    """Tracks stage transitions with justification - the 'Progression Log'"""
    __tablename__ = "deal_stage_changes"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    from_stage = Column(String(20), nullable=True)  # null for initial creation
    to_stage = Column(String(20), nullable=False)
    trigger_type = Column(String(20), nullable=False)  # 'manual', 'auto', 'call_analysis'
    trigger_call_id = Column(Integer, ForeignKey("calls.id"), nullable=True)  # The call that triggered this
    justification = Column(Text)  # AI-generated or user-provided reason
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # null if auto
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    deal = relationship("Deal", back_populates="stage_history")
    trigger_call = relationship("Call")
    user = relationship("User")


class DealStageOverride(Base):
    """Tracks when stages are skipped with explanations"""
    __tablename__ = "deal_stage_overrides"

    id = Column(Integer, primary_key=True, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=False, index=True)
    stage_change_id = Column(Integer, ForeignKey("deal_stage_changes.id"), nullable=True)  # Links to the stage change
    skipped_stage = Column(String(20), nullable=False)  # The stage that was skipped
    explanation = Column(Text, nullable=False)  # Why it was skipped
    overridden_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    deal = relationship("Deal", back_populates="stage_overrides")
    stage_change = relationship("DealStageChange")
    user = relationship("User")


class Call(Base):
    __tablename__ = "calls"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    deal_id = Column(Integer, ForeignKey("deals.id"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    transcript = Column(Text, nullable=False)
    instruction_set = Column(String(100), nullable=False)
    summary = Column(Text)
    action_items = Column(Text)  # JSON string
    next_step = Column(Text)
    determination = Column(Text)
    call_date = Column(DateTime(timezone=True))  # When the call actually happened
    sequence_num = Column(Integer)  # Order within a deal
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    workspace = relationship("Workspace", back_populates="calls")
    deal = relationship("Deal", back_populates="calls")

    __table_args__ = (
        Index("ix_call_deal_sequence", "deal_id", "sequence_num"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "deal_id": self.deal_id,
            "filename": self.filename,
            "transcript": self.transcript,
            "instruction_set": self.instruction_set,
            "summary": self.summary,
            "action_items": self.action_items,
            "next_step": self.next_step,
            "determination": self.determination,
            "call_date": self.call_date.isoformat() if self.call_date else None,
            "sequence_num": self.sequence_num,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
