"""Pure model for the Hub's menu-blueprint runtime.

The browser editor is intentionally not the authority. It only edits a graph;
this module validates the graph and later will compile it into QQ cards.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

NODE_TYPES = frozenset({"panel", "command", "url", "action", "confirm"})


@dataclass(frozen=True, slots=True)
class BlueprintEdge:
    source_node_id: str
    source_button_id: str
    target_node_id: str


@dataclass(frozen=True, slots=True)
class BlueprintGraph:
    root_node_id: str
    nodes: dict[str, dict[str, Any]]
    edges: tuple[BlueprintEdge, ...]

    def target_for(self, source_node_id: str, source_button_id: str) -> str | None:
        for edge in self.edges:
            if edge.source_node_id == source_node_id and edge.source_button_id == source_button_id:
                return edge.target_node_id
        return None


def parse_blueprint(raw: object) -> BlueprintGraph:
    """Validate graph invariants independently of the Web UI."""
    if not isinstance(raw, dict):
        raise ValueError("蓝图必须是对象")
    raw_nodes = raw.get("nodes")
    raw_edges = raw.get("edges", [])
    root = raw.get("root_node_id")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("蓝图至少需要一个节点")
    nodes: dict[str, dict[str, Any]] = {}
    for node in raw_nodes:
        if not isinstance(node, dict):
            raise ValueError("蓝图节点格式错误")
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not node_id.strip() or node_id in nodes:
            raise ValueError("蓝图节点 ID 必须唯一且非空")
        if node_type not in NODE_TYPES:
            raise ValueError("未知蓝图节点类型")
        if not isinstance(node.get("title"), str) or not node["title"].strip():
            raise ValueError("蓝图节点名称不能为空")
        nodes[node_id] = dict(node)
    if not isinstance(root, str) or root not in nodes or nodes[root]["type"] != "panel":
        raise ValueError("根节点必须是一个已有的卡片菜单节点")
    if not isinstance(raw_edges, list):
        raise ValueError("蓝图连线格式错误")
    edges: list[BlueprintEdge] = []
    occupied_ports: set[tuple[str, str]] = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            raise ValueError("蓝图连线格式错误")
        source = item.get("from")
        target = item.get("to")
        button_id = item.get("button_id")
        if source not in nodes or target not in nodes:
            raise ValueError("蓝图连线必须连接已有节点")
        if nodes[source]["type"] != "panel":
            raise ValueError("只有卡片菜单节点可以发出连线")
        if not isinstance(button_id, str) or not button_id.strip():
            raise ValueError("每条菜单连线必须绑定来源按钮")
        port = (source, button_id)
        if port in occupied_ports:
            raise ValueError("一个按钮只能连接一个目标节点")
        occupied_ports.add(port)
        edges.append(BlueprintEdge(source, button_id, target))
    return BlueprintGraph(root, nodes, tuple(edges))
