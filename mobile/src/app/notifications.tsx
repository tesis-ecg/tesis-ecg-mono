import { Bell, ChevronLeft, ChevronRight } from 'lucide-react-native'
import { useRouter } from 'expo-router'
import { useMemo, useState } from 'react'

import type { AlertStatus } from '@/features/patient/api'
import { routeForAlert } from '@/features/notifications/routeForAlert'
import { alertMeta } from '@/features/patient/deviceMeta'
import { useInfiniteAlerts } from '@/features/patient/hooks'
import type { PatientAlert } from '@/features/patient/types'
import { cn } from '@/lib/cn'
import { formatDateTime } from '@/lib/format'
import * as haptics from '@/lib/haptics'
import { ActivityIndicator, Pressable, Text, View } from '@/tw'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorState } from '@/components/ui/ErrorState'
import { Refresh } from '@/components/ui/Refresh'
import { Screen } from '@/components/ui/Screen'
import { Body, Caption, Heading } from '@/components/ui/typography'

/**
 * Los tres filtros, en el orden en que se leen.
 *
 * "Pendientes" arranca elegido y no "Todas": el paciente entra acá desde la
 * campana, que le dice cuántos avisos tiene sin responder. Abrir en la lista
 * completa lo obligaba a buscar entre los ya contestados justamente los que
 * venía a resolver.
 */
const FILTERS: { value: AlertStatus; label: string }[] = [
  { value: 'all', label: 'Todas' },
  { value: 'pending', label: 'Pendientes' },
  { value: 'answered', label: 'Respondidas' },
]

/** Qué decir cuando el filtro elegido no tiene nada para mostrar. */
const EMPTY_TEXT: Record<AlertStatus, { title: string; description: string }> = {
  all: {
    title: 'Todavía no hay notificaciones',
    description: 'Cuando haya algo para revisar, va a aparecer acá.',
  },
  pending: {
    title: 'No tenés avisos sin responder',
    description: 'Contestaste todo lo que te pedimos. Te avisamos si aparece algo nuevo.',
  },
  answered: {
    title: 'Todavía no respondiste ninguno',
    description: 'Los avisos que contestes van a quedar guardados acá.',
  },
}

export default function NotificationsScreen() {
  const router = useRouter()
  const [filter, setFilter] = useState<AlertStatus>('pending')
  const alerts = useInfiniteAlerts(filter)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const items = useMemo(() => alerts.data?.pages.flatMap((page) => page.items) ?? [], [alerts.data])
  // El mismo número que el badge de Inicio, y sale de la primera página porque
  // el backend lo cuenta sobre el total, no sobre lo que se trajo hasta acá.
  // No depende del filtro: es cuántos avisos quedan sin responder, siempre.
  const pendingTotal = alerts.data?.pages[0]?.pendingTotal ?? 0

  const refresh = async () => {
    setIsRefreshing(true)
    await alerts.refetch()
    haptics.tap()
    setIsRefreshing(false)
  }

  const openAlert = (alert: PatientAlert) => {
    const destination = routeForAlert(alert)
    if (!destination) return
    haptics.tap()
    if (destination.pathname === '/report') {
      router.push({ pathname: destination.pathname, params: destination.params })
    } else if (destination.pathname === '/report-response') {
      router.push({ pathname: destination.pathname, params: destination.params })
    } else {
      router.push(destination.pathname)
    }
  }

  /*
    Header fijo, igual que el del formulario: el título dice cuántos avisos hay
    sin responder, y con la lista scrolleando ese número se perdía apenas el
    paciente bajaba un poco. Los filtros van acá arriba por lo mismo — cambiar
    de vista no puede depender de volver al principio de la lista. El gradiente
    lo aporta `Screen`.
  */
  const header = (
    <View className="gap-4">
      <View className="flex-row items-center gap-3">
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Volver"
          onPress={() => router.back()}
          className="size-11 items-center justify-center rounded-full bg-white"
        >
          <ChevronLeft size={24} color="#172126" />
        </Pressable>
        <Heading className="flex-1 text-center font-bold">Notificaciones ({pendingTotal})</Heading>
        {/* Contrapeso del botón de volver: sin esto el título queda corrido. */}
        <View className="size-11" />
      </View>

      <View className="flex-row gap-2">
        {FILTERS.map((option) => (
          <FilterPill
            key={option.value}
            label={option.label}
            isSelected={filter === option.value}
            onPress={() => {
              if (filter === option.value) return
              haptics.selection()
              setFilter(option.value)
            }}
          />
        ))}
      </View>
    </View>
  )

  const empty = EMPTY_TEXT[filter]

  return (
    <Screen
      fixedHeader={header}
      refreshControl={<Refresh refreshing={isRefreshing} onRefresh={() => void refresh()} />}
    >
      {alerts.isPending ? (
        <View className="items-center py-16">
          <ActivityIndicator color="#0b2185" />
        </View>
      ) : alerts.isError && items.length === 0 ? (
        <Card>
          <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState icon={Bell} title={empty.title} description={empty.description} />
        </Card>
      ) : (
        <View className="gap-3">
          {items.map((alert) => (
            <NotificationCard key={alert.id} alert={alert} onPress={() => openAlert(alert)} />
          ))}

          {alerts.isError ? (
            <Card>
              <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
            </Card>
          ) : null}

          {alerts.hasNextPage ? (
            <Button
              label="Cargar más"
              variant="secondary"
              loading={alerts.isFetchingNextPage}
              onPress={() => void alerts.fetchNextPage()}
            />
          ) : null}
        </View>
      )}
    </Screen>
  )
}

/**
 * Un filtro de la fila.
 *
 * `flex-1` y no ancho por contenido: con tres pastillas de anchos distintos, la
 * fila se lee como tres botones sueltos y no como un control con tres estados.
 * El alto es el mínimo de toque de la app, no el que pide el texto.
 */
function FilterPill({
  label,
  isSelected,
  onPress,
}: {
  label: string
  isSelected: boolean
  onPress: () => void
}) {
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected: isSelected }}
      accessibilityLabel={label}
      onPress={onPress}
      className={cn(
        'min-h-11 flex-1 items-center justify-center rounded-full px-3',
        isSelected ? 'bg-primary-500' : 'border border-gray-100 bg-white',
      )}
    >
      <Text
        numberOfLines={1}
        className={cn(
          'text-[15px] font-semibold',
          isSelected ? 'text-white' : 'text-gray-700',
        )}
      >
        {label}
      </Text>
    </Pressable>
  )
}

function NotificationCard({ alert, onPress }: { alert: PatientAlert; onPress: () => void }) {
  const destination = routeForAlert(alert)
  const meta = alertMeta(alert.kind)
  const Icon = meta.icon
  const isDanger = meta.tone === 'danger'

  return (
    <Pressable
      accessibilityRole={destination ? 'button' : undefined}
      accessibilityLabel={`${meta.label}. ${alert.message}`}
      disabled={!destination}
      onPress={onPress}
    >
      <Card className="flex-row items-start gap-3">
        {/*
          El color separa las dos cosas que puede decir un aviso: el chaleco mal
          puesto es lo único que se arregla con las manos y mientras siga así no
          se graba nada, así que va en rojo. Lo demás es "contanos cómo te
          sentiste": importa, pero no es una urgencia.
        */}
        <View
          className={cn(
            'mt-0.5 size-11 items-center justify-center rounded-full',
            isDanger ? 'bg-error-100' : 'bg-primary-50',
          )}
        >
          <Icon size={22} color={isDanger ? '#88271d' : '#0b2185'} />
        </View>
        <View className="flex-1 gap-2">
          <View className="flex-row items-start justify-between gap-2">
            <Heading
              className={cn('flex-1 text-[17px]', isDanger ? 'text-error-700' : 'text-gray-900')}
            >
              {meta.label}
            </Heading>
            {alert.requiresResponse ? (
              <Badge
                label={alert.needsReport ? 'Pendiente' : 'Respondida'}
                tone={alert.needsReport ? 'warning' : 'success'}
              />
            ) : null}
          </View>
          <Body className="text-gray-700">{alert.message}</Body>
          <Caption>{formatDateTime(alert.detectedAt)}</Caption>
        </View>
        {destination ? (
          <View className="mt-2">
            <ChevronRight size={20} color="#727f87" />
          </View>
        ) : null}
      </Card>
    </Pressable>
  )
}
