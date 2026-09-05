import { Background, Controls, Handle, Position, ReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo, useState } from 'react'

const COLUMN_WIDTH = 200
const ROW_HEIGHT = 110

// Simple top-down layered layout: depth (via parent/child edges) sets the
// row, sibling order sets the column. A dedicated graph-layout library
// (dagre/elkjs) would be overkill for the shallow, mostly-linear trees
// this project produces -- see the lineage feature's design decision.
function computeLayout(nodes, edges) {
  const childrenByParentId = new Map()
  edges.forEach(({ parent_id: parentId, child_id: childId }) => {
    if (!childrenByParentId.has(parentId)) childrenByParentId.set(parentId, [])
    childrenByParentId.get(parentId).push(childId)
  })

  const rootNode = nodes.find((node) => node.parent_asset_id === null) ?? nodes[0]
  const depthById = new Map([[rootNode.id, 0]])
  const queue = [rootNode.id]

  while (queue.length > 0) {
    const currentId = queue.shift()
    const depth = depthById.get(currentId)
    for (const childId of childrenByParentId.get(currentId) ?? []) {
      if (!depthById.has(childId)) {
        depthById.set(childId, depth + 1)
        queue.push(childId)
      }
    }
  }

  const columnByDepth = new Map()
  const positions = new Map()
  nodes.forEach((node) => {
    const depth = depthById.get(node.id) ?? 0
    const column = columnByDepth.get(depth) ?? 0
    positions.set(node.id, { x: column * COLUMN_WIDTH, y: depth * ROW_HEIGHT })
    columnByDepth.set(depth, column + 1)
  })

  return positions
}

const STATUS_STYLES = {
  ACTIVE: 'border-emerald-300 bg-emerald-50 text-emerald-700',
  'PURPOSE EXPIRED': 'border-amber-300 bg-amber-50 text-amber-700',
  'GRANT REVOKED': 'border-amber-300 bg-amber-50 text-amber-700',
  QUARANTINED: 'border-red-300 bg-red-50 text-red-700',
}

// Live-computed status per node, derived only from authoritative
// backend signals -- never from a client-side clock comparison, since
// this project's expiry is evaluated against a simulated (not wall)
// clock. `state` is authoritative on its own for QUARANTINED; for
// purpose-expiry/revocation, the shared origin grant's own live
// `status` (already computed server-side by evaluate_grant_status) is
// looked up via `grantStatusById`.
function computeNodeStatus(node, grantStatusById) {
  if (node.state === 'QUARANTINED') return 'QUARANTINED'
  if (node.origin_grant_id == null) return 'ACTIVE'

  const grantStatus = grantStatusById[node.origin_grant_id]
  if (grantStatus === 'EXPIRED') return 'PURPOSE EXPIRED'
  if (grantStatus === 'REVOKED') return 'GRANT REVOKED'
  return 'ACTIVE'
}

function LineageNode({ data }) {
  const style = STATUS_STYLES[data.status] ?? STATUS_STYLES.ACTIVE

  return (
    <div className={`rounded-md border px-3 py-2 text-xs shadow-sm ${style}`}>
      <Handle type="target" position={Position.Top} />
      <p className="font-semibold text-slate-800">{data.label}</p>
      <p className="mt-0.5 font-medium">{data.status}</p>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}

const NODE_TYPES = { lineage: LineageNode }

function Field({ label, value, title }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800" title={title}>
        {value}
      </dd>
    </div>
  )
}

// A SHA-256 hex digest is 64 characters -- far too long to sit in a
// narrow detail panel usefully. Showing the first 12 hex characters is
// enough to visually confirm "this matches / doesn't match" against
// another node's fingerprint at a glance; the full value is still one
// hover away via the `title` attribute, and nothing is truncated in
// the underlying data or the API response, only this display.
function shortenFingerprint(fingerprint) {
  if (!fingerprint) return null
  return `${fingerprint.slice(0, 12)}…`
}

export default function LineageGraph({ lineage, grantStatusById, error }) {
  const [selectedNodeId, setSelectedNodeId] = useState(null)

  const { flowNodes, flowEdges, nodeById } = useMemo(() => {
    if (!lineage || lineage.nodes.length === 0) {
      return { flowNodes: [], flowEdges: [], nodeById: new Map() }
    }

    const positions = computeLayout(lineage.nodes, lineage.edges)
    const byId = new Map(lineage.nodes.map((node) => [node.id, node]))

    const nodes = lineage.nodes.map((node) => ({
      id: String(node.id),
      type: 'lineage',
      position: positions.get(node.id),
      data: { label: node.name, status: computeNodeStatus(node, grantStatusById ?? {}) },
      draggable: false,
    }))

    const edges = lineage.edges.map((edge) => ({
      id: `${edge.parent_id}-${edge.child_id}`,
      source: String(edge.parent_id),
      target: String(edge.child_id),
    }))

    return { flowNodes: nodes, flowEdges: edges, nodeById: byId }
  }, [lineage, grantStatusById])

  if (error) {
    return (
      <div role="alert" className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-800">
        <p className="font-semibold">Could not load lineage</p>
        <p className="mt-1">{error}</p>
      </div>
    )
  }

  if (!lineage || lineage.nodes.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
        Run a scenario above to see its data lineage here.
      </div>
    )
  }

  const selectedNode = selectedNodeId ? nodeById.get(Number(selectedNodeId)) : null

  return (
    <div className="grid gap-4 sm:grid-cols-[1fr_260px]">
      <div
        aria-label="Lineage graph"
        style={{ height: 320 }}
        className="rounded-lg border border-slate-200 bg-white"
      >
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={NODE_TYPES}
          onNodeClick={(_event, node) => setSelectedNodeId(node.id)}
          nodesDraggable={false}
          nodesConnectable={false}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <div className="rounded-lg border border-slate-200 bg-white p-4">
        {selectedNode ? (
          <dl className="space-y-3">
            <Field label="Asset" value={`${selectedNode.name} (#${selectedNode.id})`} />
            <Field
              label="Parent"
              value={selectedNode.parent_asset_id !== null ? `#${selectedNode.parent_asset_id}` : 'None (root asset)'}
            />
            <Field label="Root" value={`#${selectedNode.root_asset_id ?? selectedNode.id}`} />
            <Field
              label="Associated Grant"
              value={selectedNode.origin_grant_id !== null ? `#${selectedNode.origin_grant_id}` : 'None'}
            />
            <Field label="Original Purpose" value={selectedNode.origin_purpose ?? 'N/A'} />
            <Field
              label="Expiry"
              value={
                selectedNode.origin_grant_expires_at
                  ? new Date(selectedNode.origin_grant_expires_at).toLocaleString()
                  : 'N/A'
              }
            />
            <Field label="Status" value={computeNodeStatus(selectedNode, grantStatusById ?? {})} />
            <Field
              label="Fingerprint"
              value={shortenFingerprint(selectedNode.fingerprint) ?? 'N/A (original source asset)'}
              title={selectedNode.fingerprint ?? undefined}
            />
          </dl>
        ) : (
          <p className="text-sm text-slate-500">Click a node to see its details.</p>
        )}
      </div>
    </div>
  )
}
