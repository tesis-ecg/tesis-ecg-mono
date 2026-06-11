import type {
  AttentionPatient,
  DashboardAlert,
  DashboardKpis,
  DeviceWatchdogItem,
  RunningStudy,
} from '../types'
import {
  getMockAttentionPatients,
  getMockDashboardAlerts,
  getMockDashboardKpis,
  getMockDeviceWatchdog,
  getMockRunningStudies,
} from '../mocks'

export async function getDashboardKpis(): Promise<DashboardKpis> {
  // TODO(backend): GET /dashboard/kpis — ver TES-33.
  // Cuando el endpoint exista, reemplazar el mock por el call real:
  //   import { api } from '@/lib/api'
  //   const { data } = await api.get<DashboardKpis>('/dashboard/kpis')
  //   return data
  return getMockDashboardKpis()
}

export async function getDashboardAlerts(): Promise<DashboardAlert[]> {
  // TODO(backend): GET /dashboard/alerts — ver TES-34.
  //   const { data } = await api.get<DashboardAlert[]>('/dashboard/alerts')
  //   return data
  return getMockDashboardAlerts()
}

export async function getAttentionPatients(): Promise<AttentionPatient[]> {
  // TODO(backend): GET /dashboard/attention-patients — ver TES-35.
  //   const { data } = await api.get<AttentionPatient[]>('/dashboard/attention-patients')
  //   return data
  return getMockAttentionPatients()
}

export async function getRunningStudies(): Promise<RunningStudy[]> {
  // TODO(backend): GET /dashboard/running-studies — ver TES-36.
  //   const { data } = await api.get<RunningStudy[]>('/dashboard/running-studies')
  //   return data
  return getMockRunningStudies()
}

export async function getDeviceWatchdog(): Promise<DeviceWatchdogItem[]> {
  // TODO(backend): GET /dashboard/device-watchdog — ver TES-37.
  //   const { data } = await api.get<DeviceWatchdogItem[]>('/dashboard/device-watchdog')
  //   return data
  return getMockDeviceWatchdog()
}
