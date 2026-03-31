from __future__ import annotations

from kgqa.query import Neo4jExecutor


class _FakeNode:
    def __init__(self, element_id: str, labels: list[str], **properties: object):
        self.element_id = element_id
        self.labels = set(labels)
        self._properties = properties

    def items(self):
        return self._properties.items()


class _FakeRelationship:
    def __init__(
        self,
        element_id: str,
        relationship_type: str,
        start_node: _FakeNode,
        end_node: _FakeNode,
        **properties: object,
    ):
        self.element_id = element_id
        self.type = relationship_type
        self.start_node = start_node
        self.end_node = end_node
        self._properties = properties

    def items(self):
        return self._properties.items()


class _FakePath:
    def __init__(self, nodes: list[_FakeNode], relationships: list[_FakeRelationship]):
        self.nodes = nodes
        self.relationships = relationships


def test_neo4j_executor_normalizes_graph_objects(monkeypatch) -> None:
    monkeypatch.setattr("kgqa.query.Node", _FakeNode)
    monkeypatch.setattr("kgqa.query.Relationship", _FakeRelationship)
    monkeypatch.setattr("kgqa.query.Path", _FakePath)

    left = _FakeNode("n1", ["Model"], name="MONOSPACE-1200", brand="三菱")
    right = _FakeNode("n2", ["Category"], name="无机房乘客梯")
    rel = _FakeRelationship("r1", "BELONGS_TO", left, right, dataset="elevator_poc")
    path = _FakePath([left, right], [rel])

    normalized_node = Neo4jExecutor._normalize_value(left)
    normalized_rel = Neo4jExecutor._normalize_value(rel)
    normalized_path = Neo4jExecutor._normalize_value(path)

    assert normalized_node["__type__"] == "node"
    assert normalized_node["properties"]["brand"] == "三菱"
    assert normalized_rel["__type__"] == "relationship"
    assert normalized_rel["relationship_type"] == "BELONGS_TO"
    assert normalized_path["__type__"] == "path"
    assert normalized_path["nodes"][0]["properties"]["name"] == "MONOSPACE-1200"
