interface EmptyStateProps {
  icon?: string
  title: string
  description?: string
  action?: React.ReactNode
}

export function EmptyState({ icon = '○', title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <div className="text-4xl mb-4 text-white/20">{icon}</div>
      <div className="text-white/60 font-medium mb-1">{title}</div>
      {description && <div className="text-sm text-white/30 max-w-xs">{description}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
