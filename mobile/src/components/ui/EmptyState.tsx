import type { LucideIcon } from 'lucide-react-native'

import { View } from '@/tw'
import { Body, Heading } from './typography'

interface EmptyStateProps {
  icon: LucideIcon
  title: string
  description?: string
  children?: React.ReactNode
}

export function EmptyState({ icon: Icon, title, description, children }: EmptyStateProps) {
  return (
    <View className="items-center gap-3 px-4 py-10">
      <View className="size-16 items-center justify-center rounded-full bg-primary-50">
        <Icon size={30} color="#0b2185" />
      </View>
      <Heading className="text-center">{title}</Heading>
      {description ? <Body className="text-center text-gray-600">{description}</Body> : null}
      {children}
    </View>
  )
}
