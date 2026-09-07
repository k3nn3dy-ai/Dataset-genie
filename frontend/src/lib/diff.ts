// Word-level diff (LCS) for the chosen-vs-rejected view. Small inputs only.
export type DiffOp = { kind: 'same' | 'add' | 'del'; text: string }

export function wordDiff(a: string, b: string): { left: DiffOp[]; right: DiffOp[] } {
  const A = a.split(/(\s+)/).filter((s) => s.length > 0)
  const B = b.split(/(\s+)/).filter((s) => s.length > 0)
  const n = A.length
  const m = B.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const left: DiffOp[] = []
  const right: DiffOp[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (A[i] === B[j]) {
      left.push({ kind: 'same', text: A[i] })
      right.push({ kind: 'same', text: B[j] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      left.push({ kind: 'del', text: A[i] })
      i++
    } else {
      right.push({ kind: 'add', text: B[j] })
      j++
    }
  }
  while (i < n) left.push({ kind: 'del', text: A[i++] })
  while (j < m) right.push({ kind: 'add', text: B[j++] })
  return { left: merge(left), right: merge(right) }
}

function merge(ops: DiffOp[]): DiffOp[] {
  const out: DiffOp[] = []
  for (const op of ops) {
    const last = out[out.length - 1]
    if (last && last.kind === op.kind) last.text += op.text
    else out.push({ ...op })
  }
  return out
}
