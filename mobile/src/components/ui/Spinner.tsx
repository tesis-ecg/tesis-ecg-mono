import { ActivityIndicator, View } from '@/tw'
import { Callout } from './typography'

export function Spinner({ label }: { label?: string }) {
  return (
    <View className="items-center justify-center gap-3 py-10">
      <ActivityIndicator size="large" color="#0b2185" />
      {label ? <Callout className="text-gray-600">{label}</Callout> : null}
    </View>
  )
}
