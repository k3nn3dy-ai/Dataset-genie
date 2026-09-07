export function Placeholder({ title, stage }: { title: string; stage?: number }) {
  return (
    <div>
      {stage && <div className="label">STAGE {String(stage).padStart(2, '0')}</div>}
      <h1 className="font-display font-bold uppercase text-3xl">{title}</h1>
      <p className="text-muted mt-2">Screen not built yet.</p>
    </div>
  )
}
