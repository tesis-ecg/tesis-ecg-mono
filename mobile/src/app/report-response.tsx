import { Activity, PersonStanding } from 'lucide-react-native'
import { useLocalSearchParams, useRouter } from 'expo-router'
import { useMemo } from 'react'

import { ReportModalHeader } from '@/components/ReportModalHeader'
import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { ErrorState } from '@/components/ui/ErrorState'
import { Screen } from '@/components/ui/Screen'
import { Spinner } from '@/components/ui/Spinner'
import { Body, Caption, Heading } from '@/components/ui/typography'
import { useCatalogs, useReport } from '@/features/patient/hooks'
import { labelFor } from '@/features/patient/labels'
import { OTHER } from '@/features/patient/reportSchema'
import { formatDateTime } from '@/lib/format'
import { View } from '@/tw'

export default function ReportResponse() {
  const router = useRouter()
  const { reportId } = useLocalSearchParams<{ reportId?: string }>()
  const report = useReport(reportId)
  const catalogs = useCatalogs()
  const labels = useMemo(() => {
    const symptoms = new Map<string, string>()
    const activities = new Map<string, string>()
    for (const option of catalogs.data?.symptoms ?? []) symptoms.set(option.value, option.label)
    for (const option of catalogs.data?.activities ?? []) activities.set(option.value, option.label)
    return { symptoms, activities }
  }, [catalogs.data])

  const header = (
    <ReportModalHeader title="Tu respuesta" onClose={() => router.back()} />
  )

  if (report.isLoading) {
    return (
      <Screen topInset={false} fixedHeader={header}>
        <Spinner label="Cargando tu respuesta…" />
      </Screen>
    )
  }

  if (report.isError || !report.data) {
    return (
      <Screen topInset={false} fixedHeader={header}>
        <Card>
          <ErrorState error={report.error} onRetry={() => void report.refetch()} />
        </Card>
      </Screen>
    )
  }

  const symptoms = report.data.symptoms.map((slug) =>
    slug === OTHER && report.data.symptomsOther
      ? report.data.symptomsOther
      : labelFor(labels.symptoms, slug),
  )
  const activity = report.data.activityOther || labelFor(labels.activities, report.data.activity)

  return (
    <Screen topInset={false} fixedHeader={header} contentClassName="gap-4">
      {/* El momento que se respondió bajó del header al cuerpo: sin él la
          pantalla no dice de cuándo es la respuesta que se está leyendo, y en
          el historial hay varias. */}
      <View className="flex-row items-center gap-2">
        <Badge label="Respondida" tone="success" />
        <Caption>{formatDateTime(report.data.occurredAt)}</Caption>
      </View>
      <Card className="gap-3">
        <View className="flex-row items-center gap-3">
          <Activity size={22} color="#0b2185" />
          <Heading>Qué sentiste</Heading>
        </View>
        <Body>{symptoms.join(' · ') || 'No sentiste síntomas'}</Body>
      </Card>
      <Card className="gap-3">
        <View className="flex-row items-center gap-3">
          <PersonStanding size={22} color="#0b2185" />
          <Heading>Qué estabas haciendo</Heading>
        </View>
        <Body>{activity}</Body>
      </Card>
      {report.data.notes ? (
        <Card className="gap-2">
          <Caption>Tu nota</Caption>
          <Body>{report.data.notes}</Body>
        </Card>
      ) : null}
    </Screen>
  )
}
