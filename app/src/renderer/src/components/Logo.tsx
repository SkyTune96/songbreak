/** Waveform bars split into stem colours — the app mark. Also rendered to build/icon.png. */
export function Logo({ size = 24 }: { size?: number }) {
  const bars = [
    ['#ff5c8a', 9],
    ['#ffa640', 15],
    ['#9d7bff', 21],
    ['#4fd18b', 13],
    ['#3cc8e8', 7]
  ] as const
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="#171922" />
      {bars.map(([c, h], i) => (
        <rect key={i} x={5.5 + i * 4.5} y={16 - h / 2} width="3" height={h} rx="1.5" fill={c} />
      ))}
    </svg>
  )
}
