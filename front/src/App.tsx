import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'

import { AppShell } from './layouts/AppShell'
import { Spinner } from './components/Spinner'
import { ProtectedRoute } from './router/ProtectedRoute'
import { RoleRoute } from './router/RoleRoute'

const Alerts = lazy(() => import('./pages/Alerts').then((module) => ({ default: module.Alerts })))
const Dashboard = lazy(() =>
  import('./pages/Dashboard').then((module) => ({ default: module.Dashboard })),
)
const DeviceDetail = lazy(() =>
  import('./pages/DeviceDetail').then((module) => ({ default: module.DeviceDetail })),
)
const Devices = lazy(() =>
  import('./pages/Devices').then((module) => ({ default: module.Devices })),
)
const Forbidden = lazy(() =>
  import('./pages/Forbidden').then((module) => ({ default: module.Forbidden })),
)
const ForgotPassword = lazy(() =>
  import('./pages/ForgotPassword').then((module) => ({ default: module.ForgotPassword })),
)
const Login = lazy(() => import('./pages/Login').then((module) => ({ default: module.Login })))
const NotFound = lazy(() =>
  import('./pages/NotFound').then((module) => ({ default: module.NotFound })),
)
const PatientDetail = lazy(() =>
  import('./pages/PatientDetail').then((module) => ({ default: module.PatientDetail })),
)
const Patients = lazy(() =>
  import('./pages/Patients').then((module) => ({ default: module.Patients })),
)
const Studies = lazy(() =>
  import('./pages/Studies').then((module) => ({ default: module.Studies })),
)
const StudyDetail = lazy(() =>
  import('./pages/StudyDetail').then((module) => ({ default: module.StudyDetail })),
)
const Users = lazy(() => import('./pages/Users').then((module) => ({ default: module.Users })))
const VestSimulator = lazy(() =>
  import('./pages/VestSimulator').then((module) => ({ default: module.VestSimulator })),
)
const DevEcgViewer = import.meta.env.DEV
  ? lazy(() => import('./pages/DevEcgViewer').then((module) => ({ default: module.DevEcgViewer })))
  : null

function App() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-bg">
          <Spinner size="lg" />
        </div>
      }
    >
      <Routes>
        {/* Públicas (sin AppShell) */}
        <Route path="/login" element={<Login />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/403" element={<Forbidden />} />

        {/* Privadas (envueltas en AppShell) */}
        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route index element={<Dashboard />} />
            <Route path="alerts" element={<Alerts />} />
            <Route path="patients" element={<Patients />} />
            <Route path="patients/:id" element={<PatientDetail />} />
            <Route path="devices" element={<Devices />} />
            <Route path="devices/:id" element={<DeviceDetail />} />
            <Route path="studies" element={<Studies />} />
            <Route path="studies/:id" element={<StudyDetail />} />
            <Route element={<RoleRoute allow={['admin']} />}>
              <Route path="users" element={<Users />} />
              {/* Simulador de chalecos: admin-only, con entrada en el menú.
                  Antes solo se llegaba escribiendo la URL, lo que en la
                  práctica lo volvía inencontrable incluso para quien lo
                  necesitaba. No va gateado por DEV: tiene que poder validarse
                  en producción. */}
              <Route path="__sim/vest" element={<VestSimulator />} />
            </Route>
            {DevEcgViewer ? <Route path="__dev/ecg-viewer" element={<DevEcgViewer />} /> : null}
          </Route>
        </Route>

        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  )
}

export default App
