import hashlib

from ..models.data_asset import DataAsset


def compute_fingerprint(root_asset: DataAsset) -> str:
    """A SHA-256 fingerprint of a root asset's simulated content.

    Hashed from the root asset's stable identity (id, name, asset_type)
    rather than any retrieval-specific detail, so every copy traced back
    to the same original content gets the same fingerprint — the same
    way a real content hash would.

    Limitation (deliberate, not a bug): this can only ever prove two
    blobs are byte-identical. It cannot detect that a summarized,
    reworded, partially copied, or otherwise transformed piece of data
    was derived from this asset — a real adversary who edits the content
    even slightly defeats an exact hash. Detecting transformed derivatives
    would need similarity/content analysis, which is explicitly out of
    scope for this simulated MVP.
    """
    content = f"{root_asset.id}:{root_asset.name}:{root_asset.asset_type}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
