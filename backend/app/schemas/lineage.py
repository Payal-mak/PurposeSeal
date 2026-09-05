from pydantic import BaseModel

from .data_asset import DataAssetOut


class LineageEdge(BaseModel):
    parent_id: int
    child_id: int


class LineageOut(BaseModel):
    """Frontend-friendly nodes/edges representation of a full lineage
    tree, rooted at the true root asset regardless of which descendant
    was asked about. Shaped to drop directly into a graph visualization
    (e.g. @xyflow/react) without reshaping on the client."""

    root_asset_id: int
    nodes: list[DataAssetOut]
    edges: list[LineageEdge]
