export function Patients() {
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Pacientes</h1>
        <p className="text-body2 text-gray-600">Listado y gestión de pacientes monitoreados.</p>
      </header>

      <section className="rounded-xl border border-gray-100 bg-white p-6 shadow-card">
        <h2 className="text-h6 text-gray-900">Pacientes — placeholder</h2>
        <p className="text-body2 text-gray-600">
          La tabla de pacientes con filtros y búsqueda se sumará en otro ticket.
        </p>
      </section>
    </div>
  )
}
