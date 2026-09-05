import { Bell, ChevronLeft, ChevronRight } from "lucide-react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useMemo, useRef, useState } from "react";

import { routeForAlert } from "@/features/notifications/routeForAlert";
import { alertMeta } from "@/features/patient/deviceMeta";
import { useInfiniteAlerts } from "@/features/patient/hooks";
import type { PatientAlert } from "@/features/patient/types";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/format";
import * as haptics from "@/lib/haptics";
import { ActivityIndicator, Pressable, View } from "@/tw";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Refresh } from "@/components/ui/Refresh";
import { Screen } from "@/components/ui/Screen";
import { Body, Caption, Heading } from "@/components/ui/typography";

export default function NotificationsScreen() {
  const router = useRouter();
  const alerts = useInfiniteAlerts("actionable");
  const { refetch } = alerts;
  const [isRefreshing, setIsRefreshing] = useState(false);
  const hasFocusedOnce = useRef(false);
  const items = useMemo(() => {
    const seen = new Set<string>();
    return (alerts.data?.pages ?? []).flatMap((page) =>
      page.items.filter((alert) => {
        if (seen.has(alert.id)) return false;
        seen.add(alert.id);
        return true;
      }),
    );
  }, [alerts.data]);
  // `total`, y no `pendingTotal`: esta bandeja también cuenta el único aviso
  // vigente de mala colocación. Inicio conserva la cuenta clínica de siempre.
  const actionableTotal = alerts.data?.pages[0]?.total;

  useFocusEffect(
    useCallback(() => {
      // El primer fetch ya lo hace React Query. Al volver desde otra pantalla
      // se fuerza uno para que una respuesta o una recuperación no queden en caché.
      if (hasFocusedOnce.current) void refetch();
      hasFocusedOnce.current = true;

      // `signal_recovered` no manda otro push: mientras esta pantalla siga
      // abierta hay que consultar el estado para retirar el aviso por sí solo.
      const interval = setInterval(() => void refetch(), 60_000);
      return () => clearInterval(interval);
    }, [refetch]),
  );

  const refresh = async () => {
    setIsRefreshing(true);
    await alerts.refetch();
    haptics.tap();
    setIsRefreshing(false);
  };

  const openAlert = (alert: PatientAlert) => {
    const destination = routeForAlert(alert);
    if (!destination) return;
    haptics.tap();
    if (destination.pathname === "/report") {
      router.push({
        pathname: destination.pathname,
        params: destination.params,
      });
    } else if (destination.pathname === "/report-response") {
      router.push({
        pathname: destination.pathname,
        params: destination.params,
      });
    } else {
      router.push(destination.pathname);
    }
  };

  // El encabezado queda fijo para que la cantidad de tareas vigentes no se
  // pierda al recorrer una lista larga. El gradiente lo aporta `Screen`.
  const header = (
    <View className="flex-row items-center gap-3">
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Volver"
        onPress={() => router.back()}
        className="size-11 items-center justify-center rounded-full bg-white"
      >
        <ChevronLeft size={24} color="#172126" />
      </Pressable>
      <Heading className="flex-1 text-center font-bold">
        {actionableTotal === undefined
          ? "Notificaciones"
          : `Notificaciones (${actionableTotal})`}
      </Heading>
      {/* Contrapeso del botón de volver: sin esto el título queda corrido. */}
      <View className="size-11" />
    </View>
  );

  return (
    <Screen
      fixedHeader={header}
      refreshControl={
        <Refresh refreshing={isRefreshing} onRefresh={() => void refresh()} />
      }
    >
      {alerts.isPending ? (
        <View className="items-center py-16">
          <ActivityIndicator color="#0b2185" />
        </View>
      ) : alerts.isError && items.length === 0 ? (
        <Card>
          <ErrorState
            error={alerts.error}
            onRetry={() => void alerts.refetch()}
          />
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={Bell}
            title="No tenés notificaciones pendientes"
            description="No hay nada que necesites hacer ahora. Te avisamos si aparece algo nuevo."
          />
        </Card>
      ) : (
        <View className="gap-3">
          {items.map((alert) => (
            <NotificationCard
              key={alert.id}
              alert={alert}
              onPress={() => openAlert(alert)}
            />
          ))}

          {alerts.isError ? (
            <Card>
              <ErrorState
                error={alerts.error}
                onRetry={() => void alerts.refetch()}
              />
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
  );
}

function NotificationCard({
  alert,
  onPress,
}: {
  alert: PatientAlert;
  onPress: () => void;
}) {
  const destination = routeForAlert(alert);
  const meta = alertMeta(alert.kind);
  const Icon = meta.icon;
  const isDanger = meta.tone === "danger";

  return (
    <Pressable
      accessibilityRole={destination ? "button" : undefined}
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
            "mt-0.5 size-11 items-center justify-center rounded-full",
            isDanger ? "bg-error-100" : "bg-primary-50",
          )}
        >
          <Icon size={22} color={isDanger ? "#88271d" : "#0b2185"} />
        </View>
        <View className="flex-1 gap-2">
          <View className="flex-row items-start justify-between gap-2">
            <Heading
              className={cn(
                "flex-1 text-[17px]",
                isDanger ? "text-error-700" : "text-gray-900",
              )}
            >
              {meta.label}
            </Heading>
            {alert.requiresResponse ? (
              <Badge
                label={alert.needsReport ? "Pendiente" : "Respondida"}
                tone={alert.needsReport ? "warning" : "success"}
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
  );
}
