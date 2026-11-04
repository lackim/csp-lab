"""Immutable configuration for application containers supplied by the caller."""

from collections.abc import Sequence
from dataclasses import dataclass

from csp_lab.models import Protocol


@dataclass(frozen=True)
class Node:
    """A CSP address and an existing Docker image; the image owns its command."""

    address: int
    image: str

    def __post_init__(self) -> None:
        if type(self.address) is not int or not 1 <= self.address <= 16382:
            raise ValueError("Node address must be an integer between 1 and 16382.")
        if self.address in (2, 3, 4):
            raise ValueError("Node addresses 2, 3, and 4 are reserved by the lab.")
        if type(self.image) is not str or not self.image.strip():
            raise ValueError("Node image must be a nonempty string.")


def normalize_nodes(nodes: Sequence[Node], protocol: Protocol) -> tuple[Node, ...]:
    if not isinstance(nodes, Sequence) or isinstance(nodes, (str, bytes)):
        raise ValueError("Nodes must be a sequence of Node objects.")
    if len(nodes) > 8:
        raise ValueError("A lab supports at most eight custom nodes.")
    if any(type(node) is not Node for node in nodes):
        raise ValueError("Nodes must contain only Node objects.")
    normalized = tuple(sorted(nodes, key=lambda node: node.address))
    if len({node.address for node in normalized}) != len(normalized):
        raise ValueError("Custom node addresses must be distinct.")
    if protocol == 1 and any(node.address > 30 for node in normalized):
        raise ValueError("CSP v1 custom node addresses must be between 1 and 30.")
    return normalized
