import Image from "next/image";

const SIZES = {
  sm: 32,
  md: 40,
} as const;

export function Logo({ size = "md" }: { size?: keyof typeof SIZES }) {
  const px = SIZES[size];
  return (
    <Image
      src="/plane-hero.png"
      alt="SkyAssist"
      width={px}
      height={px}
      className="rounded-xl object-cover shadow-sm ring-2 ring-accent/40"
      style={{ width: px, height: px }}
      priority
    />
  );
}
