from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from app.database import Base


class Call(Base):
    __tablename__ = "calls"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    transcript = Column(Text, nullable=False)
    instruction_set = Column(String(100), nullable=False)
    summary = Column(Text)
    action_items = Column(Text)  # JSON string
    next_step = Column(Text)
    determination = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "transcript": self.transcript,
            "instruction_set": self.instruction_set,
            "summary": self.summary,
            "action_items": self.action_items,
            "next_step": self.next_step,
            "determination": self.determination,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
