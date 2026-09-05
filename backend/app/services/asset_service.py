from sqlalchemy.orm import Session

from ..models.data_asset import DataAsset
from ..models.grant import Grant
from ..schemas.data_asset import DataAssetOut


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
