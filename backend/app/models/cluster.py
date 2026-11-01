from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class NodeSummary(BaseModel):
    name: str
    # None means the node did not report a Ready condition.
    ready: bool | None
    kubelet_version: str | None


class PodSummary(BaseModel):
    namespace: str
    name: str
    phase: str | None
    node: str | None
    ready_containers: int
    total_containers: int
    restarts: int


class ClusterHealth(BaseModel):
    status: Literal["connected"]
    context: str
    server_version: str
    collected_at: datetime
    # True when the cluster held more nodes or pods than one bounded request returns.
    truncated: bool
    nodes: list[NodeSummary]
    pods: list[PodSummary]
