import enum

from sqlalchemy import Column, DateTime, Enum as SQLEnum, ForeignKey, Integer, String

from ..db.base import Base


class AssetState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"


class DataAsset(Base):
    """An original or derived piece of sensitive data.

    `parent_asset_id` is the direct predecessor (None for an original,
    root asset). `root_asset_id` is denormalized to point straight at the
    top-level ancestor so lineage/purpose checks don't need to walk the
    parent chain; it is None precisely when this row IS the root (read the
    root id as `root_asset_id or id`). `origin_grant_id` is likewise
    denormalized onto every asset — root and copies alike — so "which
    purpose justified this data" is a single-column lookup, since that
    association is the central thing PurposeSeal has to check.
    """

    __tablename__ = "data_assets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    asset_type = Column(String, nullable=False)
    parent_asset_id = Column(Integer, ForeignKey("data_assets.id"), nullable=True, index=True)
    root_asset_id = Column(Integer, ForeignKey("data_assets.id"), nullable=True, index=True)
    origin_grant_id = Column(Integer, ForeignKey("grants.id"), nullable=False, index=True)
    state = Column(SQLEnum(AssetState), nullable=False, default=AssetState.ACTIVE)
    fingerprint = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
