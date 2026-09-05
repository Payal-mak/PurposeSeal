from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import NotFoundError
from ..models.data_asset import AssetState, DataAsset
from ..models.grant import Grant
from ..schemas.data_asset import DataAssetCreate, DataAssetOut
from ..schemas.lineage import LineageEdge, LineageOut
from .audit_service import write_audit_log


def get_asset(db: Session, asset_id: int) -> DataAsset:
    asset = db.get(DataAsset, asset_id)
    if asset is None:
        raise NotFoundError(f"DataAsset {asset_id} not found", error_code="asset_not_found")
    return asset


def create_asset(db: Session, payload: DataAssetCreate) -> DataAsset:
    """Create an original/root DataAsset — source data that exists
    independently of any purpose grant. Lifecycle/provenance fields are
    entirely server-controlled (see DataAssetCreate): parent_asset_id,
    root_asset_id, and origin_grant_id are always None, and state always
    starts ACTIVE. This is the only way to create a *root* asset; a
    "retrieved" or "derived" asset can only come from the retrieval/copy
    services, which is why this schema doesn't even accept those fields.
    """
    asset = DataAsset(
        name=payload.name,
        asset_type=payload.asset_type,
        parent_asset_id=None,
        root_asset_id=None,
        origin_grant_id=None,
        state=AssetState.ACTIVE,
        fingerprint=None,
        created_at=clock.now(),
    )
    db.add(asset)

    try:
        db.flush()  # assign asset.id for the audit entry, without committing

        write_audit_log(
            db,
            event_type="SOURCE_ASSET_CREATED",
            entity_type="data_asset",
            entity_id=asset.id,
            details={"name": asset.name, "asset_type": asset.asset_type},
        )

        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(asset)
    return asset


def list_assets(
    db: Session,
    state: Optional[AssetState] = None,
    asset_type: Optional[str] = None,
    root_only: bool = False,
) -> list[DataAsset]:
    query = db.query(DataAsset)
    if state is not None:
        query = query.filter(DataAsset.state == state)
    if asset_type is not None:
        query = query.filter(DataAsset.asset_type == asset_type)
    if root_only:
        query = query.filter(DataAsset.parent_asset_id.is_(None))
    return query.order_by(DataAsset.id).all()


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
