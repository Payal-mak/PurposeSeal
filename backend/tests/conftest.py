import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.clock import clock  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import enable_sqlite_foreign_keys, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.data_asset import AssetState, DataAsset  # noqa: E402


@pytest.fixture()
def db_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = session_local()
    clock.reset()
    try:
        yield session
    finally:
        session.close()
        clock.reset()


@pytest.fixture()
def existing_asset(db_session):
    """An original/root DataAsset that pre-exists independently of any
    grant, for tests that need a valid asset_id to create a grant against."""
    asset = DataAsset(
        name="Patient Lab Result #104",
        asset_type="lab_result",
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    db_session.add(asset)
    db_session.commit()
    db_session.refresh(asset)
    return asset


@pytest.fixture()
def client(db_engine):
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    clock.reset()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    clock.reset()
