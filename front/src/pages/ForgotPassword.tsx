import { zodResolver } from '@hookform/resolvers/zod'
import { Loader2 } from 'lucide-react'
import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { Link } from 'react-router-dom'
import { z } from 'zod'

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
import { forgotPasswordRequest } from '@/features/auth/api'
import { unwrapError } from '@/lib/api'

const schema = z.object({
  email: z.string().trim().min(1, 'Ingresá tu email').email('Email inválido').max(320),
})

type ForgotPasswordValues = z.infer<typeof schema>

export function ForgotPassword() {
  const [submitted, setSubmitted] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const form = useForm<ForgotPasswordValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: '' },
  })

  const onSubmit = async ({ email }: ForgotPasswordValues) => {
    setServerError(null)
    try {
      await forgotPasswordRequest(email)
      setSubmitted(true)
    } catch (error) {
      setServerError(unwrapError(error))
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-6 py-12">
      <section className="flex w-full max-w-sm flex-col gap-6 rounded-xl border border-gray-100 bg-white p-8 shadow-sm">
        <div className="flex flex-col gap-2">
          <h1 className="text-h4 text-gray-900">Recuperar contraseña</h1>
          <p className="text-body2 text-gray-600">
            Ingresá tu email institucional y te enviaremos las instrucciones de Auth0.
          </p>
        </div>

        {submitted ? (
          <div role="status" className="rounded-md border border-success-100 bg-success-50 p-3">
            <p className="text-body3 text-success-700">
              Si existe una cuenta asociada, recibirás un email con los pasos para continuar.
            </p>
          </div>
        ) : (
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4" noValidate>
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input type="email" autoComplete="email" autoFocus {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {serverError ? (
                <p role="alert" className="text-body3 text-error-700">
                  {serverError}
                </p>
              ) : null}
              <Button type="submit" disabled={form.formState.isSubmitting}>
                {form.formState.isSubmitting ? (
                  <>
                    <Loader2 className="animate-spin" aria-hidden />
                    Enviando…
                  </>
                ) : (
                  'Enviar instrucciones'
                )}
              </Button>
            </form>
          </Form>
        )}

        <Link to="/login" className="text-center text-body2 text-primary-500 hover:underline">
          Volver al login
        </Link>
      </section>
    </main>
  )
}
