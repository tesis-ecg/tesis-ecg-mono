import type { LucideIcon } from 'lucide-react'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router-dom'

import { EmptyState } from '@/components/EmptyState'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

interface WidgetCardProps {
  title: string
  icon: LucideIcon
  /** Destino del link "Ver todos" en el header. */
  to: string
  isLoading: boolean
  isError: boolean
  isEmpty: boolean
  emptyTitle: string
  children: React.ReactNode
}

export function WidgetCard({
  title,
  icon: Icon,
  to,
  isLoading,
  isError,
  isEmpty,
  emptyTitle,
  children,
}: WidgetCardProps) {
  return (
    <Card className="flex flex-col p-5">
      <CardHeader className="flex flex-row items-center justify-between p-0 pb-3">
        <CardTitle className="flex items-center gap-2 text-h6 text-gray-900">
          <Icon className="size-4 text-gray-400" aria-hidden />
          {title}
        </CardTitle>
        <Link
          to={to}
          className="flex items-center gap-1 text-body3 text-primary-500 hover:text-primary-700"
        >
          Ver todos
          <ArrowRight className="size-3.5" aria-hidden />
        </Link>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : isError ? (
          <EmptyState icon={Icon} title="No se pudo cargar" />
        ) : isEmpty ? (
          <EmptyState icon={Icon} title={emptyTitle} />
        ) : (
          children
        )}
      </CardContent>
    </Card>
  )
}
