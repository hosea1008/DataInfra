"""Pydantic response/request models shared by the visualize API routers.

These models are the contracts for the feature endpoints added by later tasks
(envs/tables/preview, stats, columns, version progress, reset). Fields are
permissive: everything that can reasonably be absent is Optional with a
default, so partial payloads still validate.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# A JSON scalar as produced by serialize_cell/truncate_value pipelines.
JsonScalar = str | int | float | bool | None


class TableInfo(BaseModel):
    """One table entry: name plus optional catalog estimates."""

    name: str
    est_rows: int | None = None
    n_columns: int | None = None


class TableListResponse(BaseModel):
    env: str
    tables: list[TableInfo] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    table: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    est_rows: int | None = None
    limit: int = 0
    offset: int = 0


class DatasetStatItem(BaseModel):
    dataset_name: str
    value: float | None = None
    ratio: float | None = None


class DatasetTotals(BaseModel):
    """Global totals across all datasets."""

    lerobots: int | None = None
    episodes: int | None = None
    hours: float | None = None


class DatasetStatsResponse(BaseModel):
    """Per-dataset pie payloads: lerobot count / episode count / total hours.

    ``sampled``/``sample_pct`` describe the hours pie only: when the ``data``
    table is huge its aggregation runs on a TABLESAMPLE subset and counts are
    scaled-up estimates (ratios are unaffected).
    """

    cached_at: datetime | None = None
    totals: DatasetTotals = Field(default_factory=DatasetTotals)
    lerobots: list[DatasetStatItem] = Field(default_factory=list)
    episodes: list[DatasetStatItem] = Field(default_factory=list)
    hours: list[DatasetStatItem] = Field(default_factory=list)
    sampled: bool = False
    sample_pct: float | None = None


class ColumnInfoItem(BaseModel):
    name: str
    kind: str | None = None
    note: str | None = None


class ColumnListResponse(BaseModel):
    columns: list[ColumnInfoItem] = Field(default_factory=list)


class DistItem(BaseModel):
    """One bucket of a column distribution."""

    value: JsonScalar = None
    rows: int | None = None
    hours: float | None = None
    ratio_rows: float | None = None
    ratio_hours: float | None = None


class ColumnDistResponse(BaseModel):
    """Value distribution of one ``data`` column.

    ``sampled``/``sample_pct`` flag estimations produced from a TABLESAMPLE
    subset of a huge ``data`` table (rows/hours scaled back up; exact via
    refresh).
    """

    column: str
    cached_at: datetime | None = None
    items: list[DistItem] = Field(default_factory=list)
    other: DistItem | None = None
    total_rows: int | None = None
    total_hours: float | None = None
    sampled: bool = False
    sample_pct: float | None = None


class VersionProgressItem(BaseModel):
    key: str
    step_key: str | None = None
    version_no: str | int | float | None = None
    count: int | None = None
    completed_hours: float | None = None
    total_hours: float | None = None
    progress_kind: str = "count"
    ratio: float | None = None
    step_explain: str | None = None
    version_note: str | None = None


class VersionKeyInfo(BaseModel):
    """One selectable progress key (a status_statistic column)."""

    key: str
    step_key: str | None = None
    version_no: str | int | float | None = None


class VersionProgressResponse(BaseModel):
    env: str
    total_lerobots: int | None = None
    cached_at: datetime | None = None
    available: list[VersionKeyInfo] = Field(default_factory=list)
    items: list[VersionProgressItem] = Field(default_factory=list)


class DatasetCount(BaseModel):
    """One production dataset with its lerobot count (reset wizard step 1)."""

    dataset_name: str
    lerobot_count: int | None = None


class ResetDatasetsResponse(BaseModel):
    datasets: list[DatasetCount] = Field(default_factory=list)
    default_n: int = 10


class ResetPlanRequest(BaseModel):
    dataset_names: list[str]
    n: int = Field(default=10, ge=1, le=500)


class ResetVolumes(BaseModel):
    """Copy-volume estimate shown to the user before executing a reset."""

    status_rows: int = 0
    lerobot_episode_rows: int = 0
    episode_rows_est: int = 0
    meta_rows: dict[str, int] = Field(default_factory=dict)


class ResetPlanResponse(BaseModel):
    dataset_names: list[str] = Field(default_factory=list)
    n: int = 10
    lerobot_uuids: list[str] = Field(default_factory=list)
    volumes: ResetVolumes = Field(default_factory=ResetVolumes)
    warnings: list[str] = Field(default_factory=list)


class ResetExecuteRequest(BaseModel):
    dataset_names: list[str]
    n: int = Field(default=10, ge=1, le=500)
    lerobot_uuids: list[str] = Field(default_factory=list)
    force: bool = False


class ResetExecuteResponse(BaseModel):
    ok: bool
    duration_s: float | None = None
    dropped_tables: list[str] = Field(default_factory=list)
    created_tables: list[str] = Field(default_factory=list)
    copied: dict[str, int] = Field(default_factory=dict)
    status_statistic: dict[str, int] | None = None


class DatasetPathLink(BaseModel):
    path: str
    target: str


class DatasetPathItem(BaseModel):
    dataset_name: str | None = None
    sample_count: int
    dataset_path: str | None = None
    resolved_path: str | None = None
    links: list[DatasetPathLink] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DatasetPathsResponse(BaseModel):
    cached_at: datetime
    sample_size: int = 20
    items: list[DatasetPathItem] = Field(default_factory=list)
