import { useRouter } from "expo-router";
import {
  Activity,
  FileText,
  ListFilter,
  PersonStanding,
} from "lucide-react-native";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Refresh } from "@/components/ui/Refresh";
import { Screen } from "@/components/ui/Screen";
import { Spinner } from "@/components/ui/Spinner";
import { Body, Caption, Title } from "@/components/ui/typography";
import { useCatalogs, useReports } from "@/features/patient/hooks";
import { labelFor } from "@/features/patient/labels";
import type { PatientReport } from "@/features/patient/types";
import { formatDateTime } from "@/lib/format";
import * as haptics from "@/lib/haptics";
import { enterAt } from "@/lib/motion";
import { AnimatedView, Pressable, Text, View } from "@/tw";
import { cn } from "@/lib/cn";

type Filter = "todos" | "push_response" | "manual";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "todos", label: "Todos" },
  { value: "push_response", label: "Por aviso" },
  { value: "manual", label: "Que anoté yo" },
];

export default function History() {
  const router = useRouter();
  const reports = useReports();
  const catalogs = useCatalogs();
  const [filter, setFilter] = useState<Filter>("todos");
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await reports.refetch();
    haptics.tap();
    setIsRefreshing(false);
  };

  // El backend manda los slugs y el catálogo por separado; el mapa se arma acá
  // para no repetir las etiquetas en la app y que se desincronicen.
  const labels = useMemo(() => {
    const map = new Map<string, string>();
    for (const option of catalogs.data?.symptoms ?? [])
      map.set(option.value, option.label);
    for (const option of catalogs.data?.activities ?? [])
      map.set(option.value, option.label);
    return map;
  }, [catalogs.data]);

  const items = (reports.data?.items ?? []).filter(
    (report) => filter === "todos" || report.source === filter,
  );
  const hasAny = (reports.data?.total ?? 0) > 0;

  /*
    Los filtros van en la parte fija junto al título, como en Notificaciones:
    son la única forma de acotar la lista, y scrolleando quedaban fuera de vista
    justo cuando la lista se hace larga —que es cuando hacen falta—.

    La bajada se pega al título (`gap-2`) en vez de repartirse el `gap-4` del
    header: título y bajada son una sola cosa, y con la misma separación que
    tienen contra los filtros el bloque se leía como tres renglones sueltos en
    vez de un encabezado y sus controles.
  */
  const header = (
    <View className="gap-4">
      <View className="gap-2">
        <Title>Historial</Title>
        <Body className="text-gray-600">
          Todo lo que registraste. Tu médico lo ve junto a tu
          electrocardiograma.
        </Body>
      </View>

      {hasAny && (
        <View className="flex-row gap-2">
          {FILTERS.map((option) => {
            const isActive = filter === option.value;
            return (
              <Pressable
                key={option.value}
                accessibilityRole="button"
                accessibilityState={{ selected: isActive }}
                onPress={() => {
                  haptics.selection();
                  setFilter(option.value);
                }}
                className={cn(
                  "min-h-[44px] justify-center rounded-full px-4 shadow-lg",
                  isActive ? "bg-primary-500" : "bg-white",
                )}
              >
                <Text
                  className={cn(
                    "text-[15px]",
                    isActive ? "font-semibold text-white" : "text-gray-700",
                  )}
                >
                  {option.label}
                </Text>
              </Pressable>
            );
          })}
        </View>
      )}
    </View>
  );

  return (
    <Screen
      fixedHeader={header}
      refreshControl={
        <Refresh
          refreshing={isRefreshing}
          onRefresh={() => void handleRefresh()}
        />
      }
    >
      {reports.isLoading ? (
        <Spinner label="Cargando tu historial…" />
      ) : reports.isError && !reports.data ? (
        // Antes esto caía en "todavía no anotaste nada": un error de red se
        // leía como un historial vacío, que es lo contrario de lo que pasa.
        <Card>
          <ErrorState
            error={reports.error}
            onRetry={() => void reports.refetch()}
          />
        </Card>
      ) : !hasAny ? (
        <Card>
          <EmptyState
            icon={FileText}
            title="Todavía no anotaste nada"
            description="Cuando sientas algo —palpitaciones, mareo, falta de aire— registralo acá. Le sirve a tu médico para entender qué pasó en ese momento."
          >
            <Button
              label="Registrar cómo me siento"
              onPress={() => router.push("/report")}
              fullWidth={false}
              className="mt-2"
            />
          </EmptyState>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={ListFilter}
            title="Sin registros con este filtro"
            description="Probá con otro filtro para ver el resto de tus registros."
          />
        </Card>
      ) : (
        items.map((report, index) => (
          <AnimatedView key={report.id} entering={enterAt(index)}>
            <ReportCard report={report} labels={labels} />
          </AnimatedView>
        ))
      )}
    </Screen>
  );
}

function ReportCard({
  report,
  labels,
}: {
  report: PatientReport;
  labels: Map<string, string>;
}) {
  // `labelFor` degrada a una versión legible del slug. El fallback plano de
  // antes mostraba `dolor_pecho` en pantalla mientras el catálogo cargaba.
  const symptoms = report.symptoms.map((slug) => labelFor(labels, slug));
  if (report.symptomsOther) symptoms.push(report.symptomsOther);
  const activity = report.activityOther || labelFor(labels, report.activity);

  return (
    <Card className="gap-3">
      <View className="flex-row items-start justify-between gap-3">
        <Body className="flex-1 font-semibold">
          {formatDateTime(report.occurredAt)}
        </Body>
        <Badge
          label={
            report.source === "push_response" ? "Por aviso" : "Lo anoté yo"
          }
          tone={report.source === "push_response" ? "info" : "neutral"}
          className="shrink-0"
        />
      </View>

      <View className="flex-row items-start gap-3">
        <Activity size={20} color="#727f87" />
        <Body className="flex-1">{symptoms.join(" · ") || "Sin síntomas"}</Body>
      </View>

      <View className="flex-row items-start gap-3">
        <PersonStanding size={20} color="#727f87" />
        <Body className="flex-1 text-gray-700">{activity}</Body>
      </View>

      {report.notes ? (
        <View className="rounded-[14px] bg-gray-50 px-4 py-3">
          <Caption className="pb-1">Tu nota</Caption>
          <Body className="text-gray-700">{report.notes}</Body>
        </View>
      ) : null}
    </Card>
  );
}
