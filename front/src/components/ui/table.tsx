import * as React from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'

import { cn } from '@/lib/utils'

function Table({ className, ...props }: React.ComponentProps<'table'>) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table
        data-slot="table"
        className={cn(
          'w-full caption-bottom border-separate border-spacing-0 text-body2',
          className,
        )}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<'thead'>) {
  return <thead data-slot="table-header" className={cn(className)} {...props} />
}

function TableBody({ className, ...props }: React.ComponentProps<'tbody'>) {
  return <tbody data-slot="table-body" className={cn(className)} {...props} />
}

function TableFooter({ className, ...props }: React.ComponentProps<'tfoot'>) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn('bg-muted/50 font-medium', className)}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<'tr'>) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        'transition-colors hover:bg-gray-50/60 has-aria-expanded:bg-gray-50/60 data-[state=selected]:bg-gray-50',
        className,
      )}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<'th'>) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        'h-[60px] bg-primary-50 px-2 text-left align-middle text-body2 font-medium text-primary-500 first:pl-6 last:pr-6 whitespace-nowrap',
        className,
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<'td'>) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        'h-[76px] border-b border-gray-50 px-2 align-middle text-body2 text-gray-800 first:pl-6 last:pr-6 whitespace-nowrap',
        className,
      )}
      {...props}
    />
  )
}

function TableCaption({ className, ...props }: React.ComponentProps<'caption'>) {
  return (
    <caption
      data-slot="table-caption"
      className={cn('mt-4 text-body3 text-gray-600', className)}
      {...props}
    />
  )
}

interface TablePaginationProps {
  page: number
  totalPages: number
  total: number
  pageSize: number
  onPageChange: (page: number) => void
  isFetching?: boolean
  className?: string
}

/**
 * Paginación reutilizable para tablas (estilo ican-web): 4 botones chevron
 * (primera / anterior / siguiente / última) + "Página X de Y" + "Mostrando X–Y de Z".
 *
 * El componente es stateless: el padre conserva `page` y reacciona a `onPageChange`.
 * Sincronizar con query params (deep-linking) o estado local queda fuera de su alcance.
 */
function TablePagination({
  page,
  totalPages,
  total,
  pageSize,
  onPageChange,
  isFetching = false,
  className,
}: TablePaginationProps) {
  const offset = (page - 1) * pageSize
  const showingFrom = total === 0 ? 0 : offset + 1
  const showingTo = Math.min(offset + pageSize, total)

  return (
    <div
      data-slot="table-pagination"
      className={cn(
        'flex flex-col items-start justify-between gap-3 px-6 py-4 sm:flex-row sm:items-center',
        className,
      )}
    >
      <p className="text-body3 text-gray-600">
        {total === 0 ? 'Sin resultados' : `Mostrando ${showingFrom}–${showingTo} de ${total}`}
        {isFetching && ' · Actualizando…'}
      </p>
      <div className="flex items-center gap-4">
        <span className="text-body2 text-black">
          Página {page} de {totalPages}
        </span>
        <div className="flex items-center gap-2">
          <PageButton
            onClick={() => onPageChange(1)}
            disabled={page <= 1 || isFetching}
            label="Primera página"
          >
            <ChevronsLeft className="size-[18px]" aria-hidden />
          </PageButton>
          <PageButton
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1 || isFetching}
            label="Página anterior"
          >
            <ChevronLeft className="size-[18px]" aria-hidden />
          </PageButton>
          <PageButton
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages || isFetching}
            label="Página siguiente"
          >
            <ChevronRight className="size-[18px]" aria-hidden />
          </PageButton>
          <PageButton
            onClick={() => onPageChange(totalPages)}
            disabled={page >= totalPages || isFetching}
            label="Última página"
          >
            <ChevronsRight className="size-[18px]" aria-hidden />
          </PageButton>
        </div>
      </div>
    </div>
  )
}

interface PageButtonProps {
  onClick: () => void
  disabled?: boolean
  label: string
  children: React.ReactNode
}

function PageButton({ onClick, disabled, label, children }: PageButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className={cn(
        'inline-flex size-9 items-center justify-center rounded-md border transition-colors',
        disabled
          ? 'cursor-not-allowed border-gray-200 bg-gray-50 text-gray-400'
          : 'cursor-pointer border-primary-500 bg-white text-primary-500 hover:bg-primary-50',
      )}
    >
      {children}
    </button>
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
  TablePagination,
}
