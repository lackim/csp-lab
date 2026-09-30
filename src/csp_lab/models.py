"""Validated data returned by the CLI and MCP server."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

Protocol = Literal[1, 2]


class LabModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    def as_json(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


class LabState(LabModel):
    protocol: Protocol
    libcsp_version: Literal["2.1"] = Field(alias="libcspVersion")
    build: bool = Field(default=False, exclude=True)
    image: str | None = Field(default=None, exclude=True)
    project: str | None = Field(default=None, exclude=True)
    legacy_root: Path | None = Field(default=None, exclude=True)


class PingResult(LabModel):
    target: int
    reachable: StrictBool
    rtt_ms: int = Field(alias="rttMs")


class StatusResult(LabModel):
    running: bool
    state: LabState | None


class TopologyResult(LabModel):
    protocol: Protocol
    libcsp_version: Literal["2.1"] = Field(alias="libcspVersion")
    nodes: list[int]
    probe_address: int = Field(alias="probeAddress")
    transport: Literal["ZMQ"]


class DiagnoseResult(LabModel):
    protocol: Protocol
    libcsp_version: Literal["2.1"] = Field(alias="libcspVersion")
    healthy: bool
    nodes: list[PingResult]
