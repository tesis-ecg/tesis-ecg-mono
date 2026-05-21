import { Route, Routes } from 'react-router-dom'
import { AppShell } from './layouts/AppShell'
import { Dashboard } from './pages/Dashboard'
import { Patients } from './pages/Patients'
import { Devices } from './pages/Devices'
import { Studies } from './pages/Studies'
import { Research } from './pages/Research'
import { Settings } from './pages/Settings'

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="patients" element={<Patients />} />
        <Route path="devices" element={<Devices />} />
        <Route path="studies" element={<Studies />} />
        <Route path="research" element={<Research />} />
        <Route path="settings" element={<Settings />} />
        <Route
          path="*"
          element={<div className="text-h4 text-gray-900">Página no encontrada</div>}
        />
      </Route>
    </Routes>
  )
}

export default App
