// Lane C: weapon bar icons (WeaponBar.tsx). Original work drawn for this
// repo from simple shapes, not traced from the character kit or any real
// product; no logos or brand shapes. License: CC0 1.0
// (https://creativecommons.org/publicdomain/zero/1.0/), recorded here per
// AGENTS.md rule 10.

export type IconProps = { size?: number };

/** A chunky orange toy blaster with a foam dart leaving the barrel. */
export function BlasterIcon({ size = 36 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      <g stroke="#7a3a00" strokeWidth={2} strokeLinejoin="round">
        <rect x={12} y={25} width={8} height={14} rx={3} fill="#ff8a1f" transform="rotate(14 16 32)" />
        <rect x={14} y={11} width={12} height={7} rx={2.5} fill="#3fb6ff" />
        <rect x={4} y={16} width={28} height={12} rx={5} fill="#ff8a1f" />
        <rect x={30} y={19} width={7} height={6} rx={2} fill="#ffd23f" />
        <rect x={39} y={19.5} width={7} height={5} rx={2.5} fill="#3fb6ff" />
      </g>
      <rect x={44} y={19.5} width={2.5} height={5} rx={1.2} fill="#ff5a5a" />
      <rect x={7} y={18.5} width={14} height={3} rx={1.5} fill="#ffc48a" />
    </svg>
  );
}

/** A bendy foam pool noodle: a fat tube with a hollow end. */
export function NoodleIcon({ size = 36 }: IconProps) {
  const path = "M9 40 C 9 22, 30 32, 38 11";
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden>
      <path d={path} fill="none" stroke="#1d7a44" strokeWidth={11} strokeLinecap="round" />
      <path d={path} fill="none" stroke="#3ee07a" strokeWidth={8} strokeLinecap="round" />
      <path d="M11 34 C 13 26, 24 30, 31 19" fill="none" stroke="#a6f7c3" strokeWidth={2.5} strokeLinecap="round" />
      <circle cx={38} cy={11} r={4.5} fill="#2aa158" stroke="#1d7a44" strokeWidth={1.5} />
      <circle cx={38} cy={11} r={1.8} fill="#0d4d29" />
    </svg>
  );
}

