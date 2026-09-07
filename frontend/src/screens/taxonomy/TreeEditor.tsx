import clsx from 'clsx'
import { useState } from 'react'
import type { Difficulty, TopicNode } from '../../lib/types'
import { Chip, Icon, IconButton, Toggle, toneFor } from '../../components'
import { slugify } from '../../lib/format'

const DIFFS: Difficulty[] = ['easy', 'medium', 'hard']

interface Props { tree: TopicNode[]; onChange: (t: TopicNode[]) => void; taskTypes: string[]; depth: number }

/** Editable taxonomy tree: add / rename / delete, difficulty & task-type chips, negative-branch toggle, reorder. */
export function TreeEditor({ tree, onChange, taskTypes, depth }: Props) {
  const update = (id: string, fn: (n: TopicNode) => TopicNode | null) => onChange(mapTree(tree, id, fn))
  const addRoot = () => onChange([...tree, blank(null, 0, tree.length, depth)])
  return (
    <div className="flex flex-col gap-1">
      {tree.length === 0 && <div className="font-mono text-[11px] text-dim py-6 text-center">Tree is empty. Run stage 01 or add topics by hand.</div>}
      {tree.map((n, i) => <NodeRow key={n.id} node={n} index={i} siblings={tree.length} taskTypes={taskTypes} maxDepth={depth} onUpdate={update} onMove={(dir) => onChange(move(tree, i, dir))} onChangeChildren={(children) => update(n.id, (x) => ({ ...x, children }))} />)}
      <button type="button" onClick={addRoot} className="mt-2 self-start flex items-center gap-1.5 label hover:text-cyan"><Icon name="plus" size={11} /> add topic</button>
    </div>
  )
}

function NodeRow({ node, index, siblings, taskTypes, maxDepth, onUpdate, onMove, onChangeChildren }: { node: TopicNode; index: number; siblings: number; taskTypes: string[]; maxDepth: number; onUpdate: (id: string, fn: (n: TopicNode) => TopicNode | null) => void; onMove: (dir: -1 | 1) => void; onChangeChildren: (c: TopicNode[]) => void }) {
  const [open, setOpen] = useState(true)
  const [editing, setEditing] = useState(false)
  const children = node.children ?? []
  const canAddChild = node.depth < maxDepth - 1
  const addChild = () => onChangeChildren([...children, blank(node.id, node.depth + 1, children.length, maxDepth, node.is_negative)])
  const childUpdate = (id: string, fn: (n: TopicNode) => TopicNode | null) => onChangeChildren(mapTree(children, id, fn))
  return (
    <div className="flex flex-col">
      <div className={clsx('group flex items-center gap-2 h-8 rounded-btn pr-1 hover:bg-surface2/70', node.is_negative && 'text-amber')} style={{ paddingLeft: node.depth * 18 }}>
        <button type="button" onClick={() => setOpen((o) => !o)} className={clsx('w-4 h-4 flex items-center justify-center text-dim', children.length === 0 && 'invisible')}><Icon name="chevron" size={10} className={clsx('transition-transform', open && 'rotate-90')} /></button>
        <span className={clsx('w-1.5 h-1.5 rounded-full shrink-0', node.is_leaf ? 'bg-cyan' : node.depth === 0 ? 'bg-magenta' : 'bg-muted')} />
        {editing ? (
          <input autoFocus defaultValue={node.label} onBlur={(e) => { setEditing(false); onUpdate(node.id, (n) => ({ ...n, label: e.target.value || n.label, slug: slugify(e.target.value || n.label) })) }} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(false) }} className="field h-6 !py-0 flex-1 !text-[12.5px]" />
        ) : (
          <button type="button" onDoubleClick={() => setEditing(true)} className={clsx('flex-1 text-left truncate text-[13.5px]', node.depth === 0 ? 'font-semibold' : 'font-medium', node.is_leaf && 'font-mono text-[12.5px]')} title="Double-click to rename">{node.label}</button>
        )}
        {node.is_leaf && (
          <>
            <Chip tone={toneFor(node.difficulty ?? '')} onClick={() => onUpdate(node.id, (n) => ({ ...n, difficulty: DIFFS[(DIFFS.indexOf(n.difficulty ?? 'medium') + 1) % 3] }))} title="Cycle difficulty">{node.difficulty ?? 'medium'}</Chip>
            <Chip tone="default" onClick={() => onUpdate(node.id, (n) => ({ ...n, task_type: taskTypes[(taskTypes.indexOf(n.task_type ?? taskTypes[0]) + 1) % taskTypes.length] }))} title="Cycle task type">{node.task_type ?? taskTypes[0]}</Chip>
          </>
        )}
        {!node.is_leaf && <span className="font-mono text-[10px] text-dim">{countLeaves(node)} leaves</span>}
        {node.depth === 1 && <Toggle size="sm" tone="amber" checked={node.is_negative} onChange={(v) => onUpdate(node.id, (n) => setNegative(n, v))} label={<span className="font-mono text-[10px] uppercase tracking-[.1em] text-muted">neg</span>} className="!gap-1.5" />}
        <div className="flex items-center opacity-0 group-hover:opacity-100 transition-opacity">
          <IconButton icon="chevron" label="Move up" size="sm" className="-rotate-90 !h-6 !w-6" disabled={index === 0} onClick={() => onMove(-1)} />
          <IconButton icon="chevron" label="Move down" size="sm" className="rotate-90 !h-6 !w-6" disabled={index === siblings - 1} onClick={() => onMove(1)} />
          <IconButton icon="edit" label="Rename" size="sm" className="!h-6 !w-6" onClick={() => setEditing(true)} />
          {canAddChild && <IconButton icon="plus" label="Add child" size="sm" className="!h-6 !w-6" onClick={addChild} />}
          <IconButton icon="trash" label="Delete" size="sm" className="!h-6 !w-6 hover:text-red" onClick={() => onUpdate(node.id, () => null)} />
        </div>
      </div>
      {open && children.length > 0 && (
        <div className="flex flex-col">
          {children.map((c, i) => <NodeRow key={c.id} node={c} index={i} siblings={children.length} taskTypes={taskTypes} maxDepth={maxDepth} onUpdate={childUpdate} onMove={(dir) => onChangeChildren(move(children, i, dir))} onChangeChildren={(cc) => childUpdate(c.id, (x) => ({ ...x, children: cc }))} />)}
        </div>
      )}
    </div>
  )
}

function blank(parent: string | null, depth: number, order: number, maxDepth: number, negative = false): TopicNode {
  const isLeaf = depth >= maxDepth - 1
  return { id: `n_${Math.random().toString(36).slice(2, 9)}`, parent_id: parent, depth, label: isLeaf ? 'New leaf' : depth === 0 ? 'New topic' : 'New subtopic', slug: isLeaf ? 'new-leaf' : 'new-topic', difficulty: isLeaf ? 'medium' : null, task_type: isLeaf ? 'TRIAGE' : null, is_negative: negative, is_leaf: isLeaf, rows_per_leaf: null, order, children: isLeaf ? undefined : [] }
}
function mapTree(nodes: TopicNode[], id: string, fn: (n: TopicNode) => TopicNode | null): TopicNode[] {
  return nodes.flatMap((n) => {
    if (n.id === id) { const r = fn(n); return r ? [r] : [] }
    return [n.children ? { ...n, children: mapTree(n.children, id, fn) } : n]
  }).map((n, i) => ({ ...n, order: i }))
}
function move(nodes: TopicNode[], i: number, dir: -1 | 1): TopicNode[] {
  const j = i + dir
  if (j < 0 || j >= nodes.length) return nodes
  const copy = [...nodes]; const t = copy[i]; copy[i] = copy[j]; copy[j] = t
  return copy.map((n, k) => ({ ...n, order: k }))
}
function setNegative(n: TopicNode, v: boolean): TopicNode { return { ...n, is_negative: v, children: n.children?.map((c) => setNegative(c, v)) } }
export function countLeaves(n: TopicNode): number { return n.is_leaf ? 1 : (n.children ?? []).reduce((a, c) => a + countLeaves(c), 0) }
