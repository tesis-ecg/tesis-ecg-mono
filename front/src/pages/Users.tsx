import { Search, UserCog, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'

import { EmptyState } from '@/components/EmptyState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { NewUserDialog } from '@/features/users/components/NewUserDialog'
import { UserRowActions } from '@/features/users/components/UserRowActions'
import { useUsers } from '@/features/users/hooks/useUsers'
import { ROLE_LABEL } from '@/features/users/utils'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { unwrapError } from '@/lib/api'

export function Users() {
  const { data, isLoading, isError, error, refetch } = useUsers()

  const [searchInput, setSearchInput] = useState('')
  const debouncedSearch = useDebouncedValue(searchInput, 300)

  useEffect(() => {
    if (isError && error) {
      toast.error(unwrapError(error), {
        id: 'users-list-error',
        action: { label: 'Reintentar', onClick: () => void refetch() },
      })
    }
  }, [isError, error, refetch])

  const items = useMemo(() => {
    const all = data ?? []
    const q = debouncedSearch.trim().toLowerCase()
    if (!q) return all
    return all.filter(
      (u) => u.fullName.toLowerCase().includes(q) || u.email.toLowerCase().includes(q),
    )
  }, [data, debouncedSearch])

  const hasActiveFilters = Boolean(debouncedSearch)

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="text-h4 text-gray-900">Usuarios</h1>
          <p className="text-body2 text-gray-600">Gestión de cuentas y roles de la plataforma.</p>
        </div>
        <NewUserDialog />
      </header>

      <Card className="flex flex-col gap-0 overflow-hidden p-0">
        <div className="flex flex-col gap-3 px-6 pt-6 pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="flex w-full max-w-sm items-center gap-3 border-b border-gray-200 pb-3">
            <Search className="size-4 shrink-0 text-gray-800" aria-hidden />
            <input
              type="search"
              placeholder="Buscar por nombre o email"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              aria-label="Buscar usuarios"
              className="w-full border-none bg-transparent text-body2 text-black outline-none placeholder:text-gray-400"
            />
          </div>
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSearchInput('')}
              className="text-gray-600"
            >
              <X className="mr-1 size-4" aria-hidden />
              Limpiar
            </Button>
          )}
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nombre</TableHead>
              <TableHead>Email</TableHead>
              <TableHead>Rol</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead className="w-12">
                <span className="sr-only">Acciones</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={`sk-${i}`}>
                  {Array.from({ length: 5 }).map((__, j) => (
                    <TableCell key={`sk-${i}-${j}`}>
                      <Skeleton className="h-4 w-full max-w-32" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="border-b-0 p-0">
                  <EmptyState
                    icon={UserCog}
                    title="No hay usuarios"
                    description={
                      hasActiveFilters
                        ? 'No hay resultados para la búsqueda.'
                        : 'Todavía no hay usuarios. Agregá uno para empezar.'
                    }
                    action={
                      hasActiveFilters ? (
                        <Button onClick={() => setSearchInput('')} variant="outline">
                          Limpiar búsqueda
                        </Button>
                      ) : null
                    }
                  />
                </TableCell>
              </TableRow>
            ) : (
              items.map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-medium text-gray-900">{u.fullName}</TableCell>
                  <TableCell>{u.email}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{ROLE_LABEL[u.role]}</Badge>
                  </TableCell>
                  <TableCell>
                    {u.isActive ? (
                      <Badge variant="outline" className="border-success-500 text-success-500">
                        Activo
                      </Badge>
                    ) : (
                      <span className="text-gray-400">Inactivo</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <UserRowActions user={u} />
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </Card>
    </div>
  )
}
