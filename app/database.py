import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

# Support both PostgreSQL and SQLite (for local dev)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/salesclutch.db")

# Handle postgres:// vs postgresql:// (Heroku-style URLs)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite needs special connect args
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app.models import User, Workspace, WorkspaceMember, WorkspaceInvite, Session, Deal, Call, DealStageChange, DealStageOverride, DealSendBack
    Base.metadata.create_all(bind=engine)
