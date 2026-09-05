import enum

from sqlalchemy import Column, Enum as SQLEnum, ForeignKey, Integer, String

from ..db.base import Base
from ..db.types import UTCDateTime


class AssetState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"


class DataAsset(Base):
    """An original or derived piece of sensitive data.

    An original/root asset (e.g. "Patient Lab Result #104") exists
    independently of any grant — it is the protected source data a grant
    later gets issued against, not something a grant brings into being.
    Its `parent_asset_id`, `root_asset_id`, and `origin_grant_id` are all
    None.

    A retrieved/derived asset, created once a future feature implements
    retrieval, is a copy or derivative made under a specific grant:
    `parent_asset_id` points at its direct predecessor, `root_asset_id`
    is denormalized to point straight at the top-level ancestor so
    lineage/purpose checks don't need to walk the parent chain (None
    means *this row is the root*; read the root id as `root_asset_id or
    id`), and `origin_grant_id` records the grant that authorized its
    creation.
    """

    __tablename__ = "data_assets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    asset_type = Column(String, nullable=False)
    parent_asset_id = Column(Integer, ForeignKey("data_assets.id"), nullable=True, index=True)
    root_asset_id = Column(Integer, ForeignKey("data_assets.id"), nullable=True, index=True)
    origin_grant_id = Column(Integer, ForeignKey("grants.id"), nullable=True, index=True)
    state = Column(SQLEnum(AssetState), nullable=False, default=AssetState.ACTIVE)
    fingerprint = Column(String, nullable=True)
    created_at = Column(UTCDateTime, nullable=False)
