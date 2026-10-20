from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.settings import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)


def get_db():
    with Session(engine) as session:
        yield session
