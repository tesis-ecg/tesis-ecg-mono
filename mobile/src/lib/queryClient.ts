import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // El celular del paciente vive con mala señal. Reintentar dos veces cubre
      // el bache típico; más que eso solo alarga la pantalla de carga.
      retry: 2,
      staleTime: 30_000,
      refetchOnWindowFocus: true,
    },
    mutations: {
      // Una mutación es un registro clínico: reintentarla sola podría duplicar
      // lo que el paciente cargó. El reintento lo decide él.
      retry: 0,
    },
  },
})
