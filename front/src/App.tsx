import { Route, Routes } from 'react-router-dom'

function Home() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-slate-900 text-white gap-2">
      <h1 className="text-4xl font-bold">Holter Dashboard</h1>
      <p className="text-slate-400">Wearable ECG — TFG Austral</p>
    </div>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
    </Routes>
  )
}

export default App
