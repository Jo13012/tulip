interface SkeletonProps { className?: string }

export function Skeleton({ className = '' }: SkeletonProps) {
  return <div className={`animate-pulse bg-white/5 rounded-lg ${className}`} />
}

export function MetricSkeleton() {
  return (
    <div className="card-sm flex flex-col gap-2">
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-7 w-28" />
    </div>
  )
}
