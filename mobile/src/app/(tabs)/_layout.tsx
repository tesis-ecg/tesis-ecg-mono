import Tabs from 'expo-router/js-tabs'

import { TabBar, TabBarSpaceProvider } from '@/components/TabBar'

export default function TabsLayout() {
  return (
    // La barra se mide sola y publica su alto por acá; `Screen` lo lee para
    // saber cuánto aire dejar al final del scroll.
    <TabBarSpaceProvider>
      <Tabs
        // La barra la dibuja `TabBar`: la nativa se ve distinta en iOS y en
        // Android, y el requisito es que las dos se parezcan lo más posible.
        tabBar={(props) => <TabBar {...props} />}
        screenOptions={{ headerShown: false, sceneStyle: { backgroundColor: '#f6f6f6' } }}
      >
        <Tabs.Screen name="index" options={{ title: 'Inicio' }} />
        <Tabs.Screen name="device" options={{ title: 'Dispositivo' }} />
        <Tabs.Screen name="history" options={{ title: 'Historial' }} />
        <Tabs.Screen name="profile" options={{ title: 'Perfil' }} />
      </Tabs>
    </TabBarSpaceProvider>
  )
}
