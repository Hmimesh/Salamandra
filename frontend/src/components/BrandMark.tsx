interface BrandMarkProps {
  className?: string;
}

export function BrandMark({ className = "" }: BrandMarkProps) {
  return (
    <img
      className={`brand-mark ${className}`.trim()}
      src="/assets/salamandra-mark.png"
      alt=""
      aria-hidden="true"
      width="512"
      height="320"
    />
  );
}
