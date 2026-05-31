import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'

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

import { holterFormSchema, type HolterFormValues } from '../holterSchema'

interface HolterFormProps {
  defaultValues?: Partial<HolterFormValues>
  onSubmit: (values: HolterFormValues) => void
  isSubmitting: boolean
  submitLabel: string
}

export function HolterForm({
  defaultValues,
  onSubmit,
  isSubmitting,
  submitLabel,
}: HolterFormProps) {
  const form = useForm<HolterFormValues>({
    resolver: zodResolver(holterFormSchema),
    defaultValues: {
      serial: '',
      model: '',
      firmwareVersion: '',
      ...defaultValues,
    },
  })

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4">
        <FormField
          control={form.control}
          name="serial"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Serial</FormLabel>
              <FormControl>
                <Input placeholder="HOLTER-AR-001" autoComplete="off" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="model"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Modelo</FormLabel>
              <FormControl>
                <Input placeholder="SIM7080G v1" autoComplete="off" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="firmwareVersion"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Versión de firmware (opcional)</FormLabel>
              <FormControl>
                <Input placeholder="0.3.1" autoComplete="off" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="flex justify-end pt-2">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? 'Guardando…' : submitLabel}
          </Button>
        </div>
      </form>
    </Form>
  )
}
