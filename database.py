import os
from contextlib import contextmanager
from sqlalchemy.orm import sessionmaker, Session as DBSession
from models import init_db

DATABASE_URL = os.environ["DATABASE_URL"]

_engine = init_db(DATABASE_URL)
_SessionFactory = sessionmaker(bind=_engine)


@contextmanager
def get_db() -> DBSession:
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
