from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.errors import NotFoundError
from ..models.data_asset import DataAsset
from ..models.grant import Grant
from ..schemas.data_asset import DataAssetOut
from ..schemas.lineage import LineageEdge, LineageOut


def get_asset(db: Session, asset_id: int) -> DataAsset:
    asset = db.get(DataAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"DataAsset {asset_id} not found", error_code="asset_not_found")
    return asset


def to_data_asset_out(db: Session, asset: DataAsset) -> DataAssetOut:
    """Serialize a DataAsset, enriching it with its purpose seal by
    joining to the origin grant at read time rather than duplicating
    purpose/expiry onto the asset row itself. Answers, without storing
    any of it twice: where did this data originate (root_asset_id /
    effective_root_asset_id), which grant authorized it
    (origin_grant_id), what purpose justified it (origin_purpose), and
    when does that purpose expire (origin_grant_expires_at)."""
    origin_purpose = None
    origin_grant_expires_at = None

    if asset.origin_grant_id is not None:
        grant = db.get(Grant, asset.origin_grant_id)
        if grant is not None:
            origin_purpose = grant.purpose
            origin_grant_expires_at = grant.expires_at

    return DataAssetOut(
        id=asset.id,
        name=asset.name,
        asset_type=asset.asset_type,
        parent_asset_id=asset.parent_asset_id,
        root_asset_id=asset.root_asset_id,
        effective_root_asset_id=asset.root_asset_id or asset.id,
        origin_grant_id=asset.origin_grant_id,
        origin_purpose=origin_purpose,
        origin_grant_expires_at=origin_grant_expires_at,
        state=asset.state,
        fingerprint=asset.fingerprint,
        created_at=asset.created_at,
    )


def get_lineage(db: Session, asset_id: int) -> LineageOut:
    """Lineage for the whole tree containing `asset_id`, rooted at its
    true root regardless of which descendant was asked about. A single
    query suffices because `root_asset_id` is denormalized onto every
    descendant (not just immediate children) — no recursive walk needed.
    """
    asset = get_asset(db, asset_id)
    root_id = asset.root_asset_id or asset.id

    nodes = (
        db.query(DataAsset)
        .filter(or_(DataAsset.id == root_id, DataAsset.root_asset_id == root_id))
        .order_by(DataAsset.id)
        .all()
    )

    edges = [
        LineageEdge(parent_id=node.parent_asset_id, child_id=node.id)
        for node in nodes
        if node.parent_asset_id is not None
    ]

    return LineageOut(
        root_asset_id=root_id,
        nodes=[to_data_asset_out(db, node) for node in nodes],
        edges=edges,
    )
