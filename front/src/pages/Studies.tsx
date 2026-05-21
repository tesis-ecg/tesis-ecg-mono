export function Studies() {
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Estudios</h1>
        <p className="text-body2 text-gray-600">Registros ECG y sesiones de monitoreo.</p>
      </header>

      <section className="rounded-xl border border-gray-100 bg-white p-6 shadow-card">
        <h2 className="text-h6 text-gray-900">Estudios — placeholder</h2>
        <p className="text-body2 text-gray-600">
          El visor de ECG y el detalle de estudios se sumarán en otro ticket.
        </p>
      </section>
    </div>
  )
}
