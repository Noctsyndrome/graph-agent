import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";

import type { GraphActiveTypes, SchemaGraphData, SchemaGraphLink, SchemaGraphNode } from "../types";

type GraphNode = SchemaGraphNode & {
  active: boolean;
  color: string;
  textColor: string;
  x?: number;
  y?: number;
  fx?: number;
  fy?: number;
};

type GraphLink = SchemaGraphLink & {
  active: boolean;
  color: string;
};

export function SchemaGraphView({
  graph,
  activeTypes,
  fitRequestKey,
}: {
  graph: SchemaGraphData | null;
  activeTypes: GraphActiveTypes;
  fitRequestKey: number;
}) {
  const FALLBACK_WIDTH = 320;
  const FALLBACK_HEIGHT = 420;
  const FIT_PADDING = 84;
  const FIT_ZOOM_FACTOR = 0.92;
  const graphRef = useRef<any>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const shouldFitOnStopRef = useRef(false);
  const [isSettling, setIsSettling] = useState(true);
  const [size, setSize] = useState({ width: FALLBACK_WIDTH, height: FALLBACK_HEIGHT });

  const data = useMemo(() => {
    if (!graph) {
      return { nodes: [] as GraphNode[], links: [] as GraphLink[] };
    }
    const activeEntities = new Set(activeTypes.entities);
    const activeRelationships = new Set(activeTypes.relationships);
    const nodes = graph.nodes.map(
      (node): GraphNode => ({
        ...node,
        active: activeEntities.has(node.entity_name),
        color: activeEntities.has(node.entity_name) ? "#111827" : "#d6d3d1",
        textColor: activeEntities.has(node.entity_name) ? "#111827" : "#78716c",
      }),
    );
    const links = graph.links.map(
      (link): GraphLink => ({
        ...link,
        active: activeRelationships.has(link.label),
        color: activeRelationships.has(link.label) ? "#0f172a" : "rgba(120, 113, 108, 0.28)",
      }),
    );
    return {
      nodes: nodes.sort((left, right) => Number(left.active) - Number(right.active)),
      links: links.sort((left, right) => Number(left.active) - Number(right.active)),
    };
  }, [activeTypes.entities, activeTypes.relationships, graph]);

  const handleEngineStop = useCallback(() => {
    if (!graphRef.current || !shouldFitOnStopRef.current || !data.nodes.length) {
      return;
    }
    shouldFitOnStopRef.current = false;
    for (const node of data.nodes) {
      node.fx = node.x;
      node.fy = node.y;
    }
    graphRef.current.zoomToFit(280, FIT_PADDING);
    const currentZoom = graphRef.current.zoom();
    if (typeof currentZoom === "number" && Number.isFinite(currentZoom)) {
      graphRef.current.zoom(currentZoom * FIT_ZOOM_FACTOR, 120);
    }
    window.setTimeout(() => {
      setIsSettling(false);
    }, 140);
  }, [data.nodes]);

  const handleNodeDragEnd = useCallback((node: GraphNode) => {
    node.fx = node.x;
    node.fy = node.y;
  }, []);

  useEffect(() => {
    if (!graphRef.current) {
      return;
    }
    graphRef.current.d3Force("charge").strength(-280);
    graphRef.current.d3Force("link").distance(108);
  }, []);

  useEffect(() => {
    if (!graphRef.current || !data.nodes.length) {
      return;
    }
    setIsSettling(true);
    for (const node of data.nodes) {
      delete node.fx;
      delete node.fy;
    }
    shouldFitOnStopRef.current = true;
    graphRef.current.d3ReheatSimulation();
    graphRef.current.resumeAnimation?.();
  }, [data.nodes, fitRequestKey]);

  useEffect(() => {
    const element = shellRef.current;
    if (!element) {
      return;
    }
    const syncSize = () => {
      const rect = element.getBoundingClientRect();
      setSize({
        width: Math.max(FALLBACK_WIDTH, Math.floor(rect.width)),
        height: Math.max(FALLBACK_HEIGHT, Math.floor(rect.height)),
      });
    };
    syncSize();
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      const { width, height } = entry.contentRect;
      setSize({
        width: Math.max(FALLBACK_WIDTH, Math.floor(width)),
        height: Math.max(FALLBACK_HEIGHT, Math.floor(height)),
      });
    });
    observer.observe(element);
    window.addEventListener("resize", syncSize);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", syncSize);
    };
  }, []);

  if (!graph) {
    return <div className="graph-empty">请先选择场景，才能查看图谱结构。</div>;
  }

  return (
    <div className="graph-view">
      <div ref={shellRef} className={`graph-canvas-shell ${isSettling ? "is-settling" : ""}`}>
        {isSettling ? <div className="graph-canvas-placeholder">正在整理图谱布局...</div> : null}
        <ForceGraph2D
          ref={graphRef}
          width={size.width}
          height={size.height}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          nodeRelSize={6}
          warmupTicks={24}
          cooldownTicks={90}
          cooldownTime={2200}
          onEngineStop={handleEngineStop}
          linkDirectionalArrowLength={4}
          linkDirectionalArrowRelPos={1}
          linkCurvature={0.1}
          enablePanInteraction
          enableZoomInteraction
          enableNodeDrag
          onNodeDragEnd={(node) => {
            handleNodeDragEnd(node as GraphNode);
          }}
          nodeCanvasObject={(node, context, globalScale) => {
            const graphNode = node as GraphNode;
            const label = graphNode.label || graphNode.entity_name;
            const fontSize = Math.max(11, 14 / globalScale);
            context.beginPath();
            context.arc(graphNode.x ?? 0, graphNode.y ?? 0, 8, 0, 2 * Math.PI, false);
            context.fillStyle = graphNode.color;
            context.fill();

            context.font = `600 ${fontSize}px Geist, sans-serif`;
            context.fillStyle = graphNode.textColor;
            context.textAlign = "center";
            context.textBaseline = "top";
            context.fillText(label, graphNode.x ?? 0, (graphNode.y ?? 0) + 12);
          }}
          nodePointerAreaPaint={(node, color, context) => {
            const graphNode = node as GraphNode;
            context.fillStyle = color;
            context.beginPath();
            context.arc(graphNode.x ?? 0, graphNode.y ?? 0, 16, 0, 2 * Math.PI, false);
            context.fill();
          }}
          nodeLabel={(node) => {
            const graphNode = node as GraphNode;
            return `${graphNode.entity_name}\n${graphNode.properties.join(", ")}`;
          }}
          linkColor={(link) => (link as GraphLink).color}
          linkWidth={(link) => ((link as GraphLink).active ? 2.4 : 1)}
          linkLabel={(link) => {
            const graphLink = link as GraphLink;
            const detail = graphLink.cardinality ? ` (${graphLink.cardinality})` : "";
            return `${graphLink.label}${detail}`;
          }}
        />
      </div>
    </div>
  );
}
