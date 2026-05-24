import { zodResolver } from '@hookform/resolvers/zod'
import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { z } from 'zod'

import loginBanner from '@/assets/login-banner.jpg'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { useAuth } from '@/features/auth/AuthContext'
import { cn } from '@/lib/utils'

const schema = z.object({
  email: z.string().min(1, 'Ingresá tu email').email('Email inválido'),
  password: z.string().min(1, 'Ingresá tu contraseña'),
})

type LoginFormValues = z.infer<typeof schema>

function parseAuthError(err: unknown): string {
  const status = (err as { response?: { status?: number } })?.response?.status
  if (status === 401) return 'Email o contraseña incorrectos.'
  if (status === 403) return 'Tu usuario está inactivo o bloqueado.'
  if (status && status >= 500) return 'El servidor no responde. Probá de nuevo en unos minutos.'
  return 'No pudimos completar el ingreso. Verificá tu conexión e intentá de nuevo.'
}

export function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [serverError, setServerError] = useState<string | null>(null)

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '', password: '' },
    mode: 'onSubmit',
  })

  const onSubmit = async (values: LoginFormValues) => {
    setServerError(null)
    try {
      await login(values.email, values.password)
      const from = searchParams.get('from')
      navigate(from && from.startsWith('/') ? from : '/', { replace: true })
    } catch (err) {
      setServerError(parseAuthError(err))
    }
  }

  const submitting = form.formState.isSubmitting

  return (
    <main className="grid min-h-screen bg-white lg:grid-cols-[minmax(360px,38%)_1fr]">
      <section className="flex items-center justify-center px-6 py-12 sm:px-12">
        <div className="flex w-full max-w-sm flex-col gap-8">
          <div className="flex flex-col gap-2">
            <span className="text-h3 font-semibold text-primary-500">Holter</span>
            <span className="text-body2 text-gray-600">Monitoreo cardíaco continuo</span>
          </div>

          <div className="flex flex-col gap-1">
            <h1 className="text-h4 text-gray-800">Te damos la bienvenida</h1>
            <p className="text-body2 text-gray-600">
              Ingresá con tu cuenta para acceder al panel clínico.
            </p>
          </div>

          {serverError ? (
            <div
              role="alert"
              className="rounded-md border border-error-100 bg-error-50 px-3 py-2 text-body3 text-error-700"
            >
              {serverError}
            </div>
          ) : null}

          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-body3 text-gray-800">Email</FormLabel>
                    <FormControl>
                      <Input
                        type="email"
                        autoComplete="email"
                        autoFocus
                        placeholder="medico@hospital.com"
                        className="h-11 border-gray-200 bg-white text-body2 text-gray-900 placeholder:text-gray-400 focus-visible:border-primary-300 focus-visible:ring-2 focus-visible:ring-primary-100"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className="text-helper text-error-700" />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center justify-between">
                      <FormLabel className="text-body3 text-gray-800">Contraseña</FormLabel>
                      <Link
                        to="/forgot-password"
                        className="text-helper text-primary-500 hover:underline"
                      >
                        ¿Olvidaste tu contraseña?
                      </Link>
                    </div>
                    <FormControl>
                      <Input
                        type="password"
                        autoComplete="current-password"
                        placeholder="••••••••"
                        className="h-11 border-gray-200 bg-white text-body2 text-gray-900 placeholder:text-gray-400 focus-visible:border-primary-300 focus-visible:ring-2 focus-visible:ring-primary-100"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage className="text-helper text-error-700" />
                  </FormItem>
                )}
              />

              <Button
                type="submit"
                disabled={submitting}
                className={cn(
                  'mt-2 h-11 w-full bg-primary-500 text-body2 font-medium text-white shadow-sm transition-colors',
                  'hover:bg-primary-600 focus-visible:ring-2 focus-visible:ring-primary-300',
                )}
              >
                {submitting ? (
                  <>
                    <Loader2 size={18} className="animate-spin" />
                    Ingresando…
                  </>
                ) : (
                  'Ingresar'
                )}
              </Button>
            </form>
          </Form>

          <p className="text-helper text-gray-500">
            ¿No tenés cuenta? Tu institución debe darte de alta.
          </p>
        </div>
      </section>

      <aside aria-hidden className="relative hidden overflow-hidden bg-gray-50 lg:block">
        <img src={loginBanner} alt="" className="absolute inset-0 h-full w-full object-cover" />
        <div className="absolute inset-0 bg-gradient-to-tr from-primary-900/70 via-primary-700/35 to-transparent" />
        <div className="absolute inset-x-0 bottom-0 flex flex-col gap-2 px-12 pb-12 text-white">
          <p className="text-h4 font-semibold">Holter wearable</p>
          <p className="max-w-md text-body2 text-white/85">
            Adquisición continua de ECG y análisis remoto para profesionales de la salud.
          </p>
        </div>
      </aside>
    </main>
  )
}
