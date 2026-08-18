import { api } from '@/lib/api'

import type {
  AttentionPatient,
  DashboardAlert,
  DashboardKpis,
  DeviceWatchdogItem,
  RunningStudy,
} from '../types'

export async function getDashboardKpis(): Promise<DashboardKpis> {
  const { data } = await api.get<DashboardKpis>('/dashboard/kpis')
  return data
}

export async function getDashboardAlerts(): Promise<DashboardAlert[]> {
  const { data } = await api.get<DashboardAlert[]>('/dashboard/alerts')
  return data
}

export async function getAttentionPatients(): Promise<AttentionPatient[]> {
  const { data } = await api.get<AttentionPatient[]>('/dashboard/attention-patients')
  return data
}

export async function getRunningStudies(): Promise<RunningStudy[]> {
  const { data } = await api.get<RunningStudy[]>('/dashboard/running-studies')
  return data
}

export async function getDeviceWatchdog(): Promise<DeviceWatchdogItem[]> {
  const { data } = await api.get<DeviceWatchdogItem[]>('/dashboard/device-watchdog')
  return data
}
