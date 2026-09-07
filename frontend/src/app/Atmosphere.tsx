import { Skyline } from './Skyline'

// Fixed, pointer-events-none layers behind and over the app.
// z-order: glows < skyline < rain < (lattice lives in main) < scanlines < grain < vignette. Hazard stripe is in Layout.
const GRAIN = `url("data:image/svg+xml;utf8,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220"><filter id="n"><feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" stitchTiles="stitch"/><feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 .9 0"/></filter><rect width="100%" height="100%" filter="url(#n)"/></svg>',
)}")`

export function Atmosphere() {
  return (
    <>
      {/* Behind content */}
      <div aria-hidden className="fixed inset-0 z-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-[20%] -left-[10%] w-[60vw] h-[60vw] rounded-full" style={{ background: 'radial-gradient(circle, rgba(0,240,255,.16) 0%, rgba(0,240,255,.05) 35%, transparent 65%)' }} />
        <div className="absolute -bottom-[25%] -right-[10%] w-[65vw] h-[65vw] rounded-full" style={{ background: 'radial-gradient(circle, rgba(255,43,214,.16) 0%, rgba(255,43,214,.05) 35%, transparent 65%)' }} />
        <Skyline />
        <div className="absolute inset-0 rain-a opacity-90" />
        <div className="absolute inset-0 rain-b opacity-80" />
      </div>
      {/* Over content */}
      <div aria-hidden className="fixed inset-0 z-[60] pointer-events-none">
        <div className="absolute inset-0 scanlines opacity-70" />
        <div className="absolute inset-0 mix-blend-overlay" style={{ backgroundImage: GRAIN, opacity: 0.06 }} />
        <div className="absolute inset-0 vignette" />
      </div>
    </>
  )
}
