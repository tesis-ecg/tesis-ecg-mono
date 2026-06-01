import { Route, Routes } from 'react-router-dom'

import { AppShell } from './layouts/AppShell'
import { Dashboard } from './pages/Dashboard'
import { DevEcgViewer } from './pages/DevEcgViewer'
import { DeviceDetail } from './pages/DeviceDetail'
import { Devices } from './pages/Devices'
import { Forbidden } from './pages/Forbidden'
import { ForgotPassword } from './pages/ForgotPassword'
import { Login } from './pages/Login'
import { NotFound } from './pages/NotFound'
import { PatientDetail } from './pages/PatientDetail'
import { Patients } from './pages/Patients'
import { Research } from './pages/Research'
import { Settings } from './pages/Settings'
import { Studies } from './pages/Studies'
import { StudyDetail } from './pages/StudyDetail'
import { ProtectedRoute } from './router/ProtectedRoute'
import { RoleRoute } from './router/RoleRoute'

function App() {
  return (
    <Routes>
      {/* Públicas (sin AppShell) */}
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/403" element={<Forbidden />} />

      {/* Privadas (envueltas en AppShell) */}
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="patients" element={<Patients />} />
          <Route path="patients/:id" element={<PatientDetail />} />
          <Route path="devices" element={<Devices />} />
          <Route path="devices/:id" element={<DeviceDetail />} />
          <Route path="studies" element={<Studies />} />
          <Route path="studies/:id" element={<StudyDetail />} />
          <Route element={<RoleRoute allow={['investigador']} />}>
            <Route path="research" element={<Research />} />
          </Route>
          <Route path="settings" element={<Settings />} />
          <Route path="__dev/ecg-viewer" element={<DevEcgViewer />} />
        </Route>
      </Route>

      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

export default App
