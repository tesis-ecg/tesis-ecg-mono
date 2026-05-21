export function Devices() {
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Dispositivos</h1>
        <p className="text-body2 text-gray-600">Inventario y estado de los Holter desplegados.</p>
      </header>

      <section className="rounded-xl border border-gray-100 bg-white p-6 shadow-card">
        <h2 className="text-h6 text-gray-900">Dispositivos — placeholder</h2>
        <p className="text-body2 text-gray-600">
          El listado de dispositivos con batería, conectividad SIM y último envío se sumará en otro
          ticket.
        </p>
      </section>
    </div>
  )
}
