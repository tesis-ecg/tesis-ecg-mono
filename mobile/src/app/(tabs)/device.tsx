import { Image } from 'expo-image'
import { Barcode, Battery, Clock, CloudUpload, Cpu, Info, Wifi } from 'lucide-react-native'
import type { LucideIcon } from 'lucide-react-native'
import { useState } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorState } from '@/components/ui/ErrorState'
import { Meter } from '@/components/ui/Meter'
import { Refresh } from '@/components/ui/Refresh'
import { Screen } from '@/components/ui/Screen'
import { Spinner } from '@/components/ui/Spinner'
import { Body, Caption, Title } from '@/components/ui/typography'
import { DEVICE_STATE, type DeviceTone } from '@/features/patient/deviceMeta'
import { useDevice, useVestMisplaced } from '@/features/patient/hooks'
import { formatDateTime, formatRelativeTime } from '@/lib/format'
import * as haptics from '@/lib/haptics'
import { enterAt } from '@/lib/motion'
import { AnimatedView, View } from '@/tw'

const VEST = require('@/assets/images/chaleco.png')

/**
 * Alto de la foto del chaleco.
 *
 * Fijo y con `contain`: el ancho de la pantalla cambia entre un iPhone SE y un
 * Max, y dejando que la foto se escale por ancho el chaleco crecía hasta
 * empujar el resto de la pantalla fuera de la vista en los equipos grandes.
 */
const VEST_HEIGHT = 260

/**
 * Cuánto monta la tarjeta de estado sobre el pie de la foto.
 *
 * El número parece grande porque no todo es chaleco: el PNG trae ~4 % de alto
 * transparente abajo, que con `VEST_HEIGHT` son unos 11 pt de aire dentro del
 * propio contenedor. Los primeros 11 pt de solape no tapan nada, así que lo que
 * la tarjeta se come de chaleco es la diferencia.
 */
const CARD_OVERLAP = 32

/** Aire entre la pastilla del estado y el techo de la tarjeta. */
const PILL_GAP = 8

/** Separación entre los widgets de la grilla, en las dos direcciones. */
const GRID_GAP = 12

export default function Device() {
  const device = useDevice()
  const isVestMisplaced = useVestMisplaced()
  const [isRefreshing, setIsRefreshing] = useState(false)

  const handleRefresh = async () => {
    setIsRefreshing(true)
    await device.refetch()
    haptics.tap()
    setIsRefreshing(false)
  }

  const state = device.data?.state ?? 'none'
  const meta = DEVICE_STATE[state]

  return (
    <Screen
      refreshControl={<Refresh refreshing={isRefreshing} onRefresh={() => void handleRefresh()} />}
    >
      <Title className="pt-2 pb-1">Dispositivo</Title>

      {device.isLoading ? (
        <Spinner label="Consultando tu chaleco…" />
      ) : device.isError && !device.data ? (
        <Card>
          <ErrorState error={device.error} onRetry={() => void device.refetch()} />
        </Card>
      ) : !device.data?.hasDevice ? (
        <Card>
          <EmptyState
            icon={Cpu}
            title="Todavía no tenés chaleco"
            description="Cuando tu médico te entregue el equipo y lo asigne a tu nombre, vas a ver acá su estado."
          />
        </Card>
      ) : (
        <>
          {/*
            La foto y la tarjeta son una sola pieza y por eso van en una misma
            `View`: entre ellas no hay separación sino solape, y `Screen` pone
            16 pt entre todo lo que cuelgue directo de él. Agrupadas, esos 16 pt
            quedan donde tienen que quedar —contra el título y contra la
            grilla— y el solape se resuelve acá adentro.
          */}
          <View>
            <VestPhoto
              label={meta.label}
              tone={meta.tone}
              description={meta.description}
              isLive={state === 'recording'}
            />

            {/*
              La tarjeta monta sobre el pie de la foto. Va después en el árbol,
              que es lo que la deja pintada encima en iOS, y con `zIndex` para
              que en Android pase lo mismo: ahí el orden de dibujo lo decide la
              elevación, no el orden de los hijos.
            */}
            <AnimatedView entering={enterAt(1)} style={{ marginTop: -CARD_OVERLAP, zIndex: 1 }}>
              <Card className="gap-3">
                <View className="flex-row items-center justify-between gap-2">
                  <Caption className="shrink">Estado del equipo</Caption>
                  {/*
                    El chaleco mal colocado no es un estado más del equipo: el
                    equipo puede estar grabando y transmitiendo perfecto y no
                    registrar nada igual, porque no hace contacto con la piel.
                    Por eso convive con la pastilla de la foto en vez de
                    reemplazarla, y es lo único rojo de la pantalla.
                  */}
                  {isVestMisplaced ? (
                    <Badge label="Chaleco mal colocado" tone="danger" className="shrink-0" />
                  ) : null}
                </View>

                {/*
                  La explicación del estado sobrevive a la card que la foto
                  reemplazó: en "sin enviar datos" es lo único que le dice al
                  paciente qué revisar, y una pastilla de dos palabras no
                  alcanza para eso.
                */}
                <Body className="text-gray-700">{meta.description}</Body>

                {isVestMisplaced ? (
                  <Body className="text-error-700">
                    Acomodate el chaleco: mientras no haga contacto con la piel, lo que registre no
                    sirve.
                  </Body>
                ) : null}
              </Card>
            </AnimatedView>
          </View>

          {/*
            Los cuatro datos del equipo, en widgets cuadrados de 2×2 en vez de
            una lista de filas. Cada uno responde una pregunta distinta y el
            paciente entra a mirar una sola: en fila había que recorrerlas todas
            de arriba abajo, y en grilla se apunta directo a la que interesa.

            El ancho va por `flexBasis` al 47 % con `flexGrow`: los dos de cada
            fila entran con la separación en el medio y después se reparten lo
            que sobra, así que la grilla se acomoda sola de un SE a un Max sin
            saber cuánto mide la pantalla.
          */}
          <View className="flex-row flex-wrap" style={{ gap: GRID_GAP }}>
            <StatWidget
              index={2}
              icon={Battery}
              label="Batería"
              value={
                device.data.batteryPercent === null ? 'Sin dato' : `${device.data.batteryPercent}%`
              }
              meter={device.data.batteryPercent}
            />
            <StatWidget
              index={3}
              icon={CloudUpload}
              label="Último envío"
              value={formatRelativeTime(device.data.lastDataReceivedAt)}
            />
            <StatWidget
              index={4}
              icon={Wifi}
              label="Última conexión"
              value={formatRelativeTime(device.data.lastSeenAt)}
            />
            <StatWidget
              index={5}
              icon={Clock}
              label={device.data.studyStartedAt ? 'Estudio iniciado' : 'Estudio'}
              value={
                device.data.studyStartedAt
                  ? formatRelativeTime(device.data.studyStartedAt)
                  : 'Sin estudio'
              }
            />
          </View>

          {/*
            La fecha exacta del arranque, al pie de la grilla: el widget dice
            "hace 3 horas", que es lo que se mira de un vistazo, y el dato
            preciso es el que el paciente le tiene que leer al consultorio.
          */}
          {device.data.studyStartedAt ? (
            <AnimatedView entering={enterAt(6)}>
              <Caption className="px-1">
                Tu estudio arrancó el {formatDateTime(device.data.studyStartedAt)}.
              </Caption>
            </AnimatedView>
          ) : null}

          {/*
            El número de equipo va suelto y a todo el ancho: no se compara con
            nada de la grilla —no es una medición que cambie— y es lo que el
            paciente tiene que leerle al consultorio por teléfono, así que se
            lee de una sin partirse en dos renglones.
          */}
          <AnimatedView entering={enterAt(7)}>
            <Card className="flex-row items-center gap-4">
              <View className="size-11 items-center justify-center rounded-full bg-gray-50">
                <Barcode size={20} color="#5c6b74" />
              </View>
              <View className="flex-1 gap-0.5">
                <Caption>Número de equipo</Caption>
                <Body className="font-semibold">{device.data.serial ?? '—'}</Body>
                {device.data.model ? (
                  <Caption className="text-gray-500">{device.data.model}</Caption>
                ) : null}
              </View>
            </Card>
          </AnimatedView>

          {/*
            El chaleco envía por lotes, no continuo: sin esta explicación, un
            "hace 50 minutos" en el último envío parece una falla y el paciente
            llama al consultorio por algo que funciona bien.
          */}
          <AnimatedView entering={enterAt(8)}>
            <Card className="flex-row gap-3 bg-info-100">
              <Info size={22} color="#0d279b" />
              <Body className="flex-1 text-info-700">
                Tu chaleco guarda todo lo que registra y lo envía cada una hora por el WiFi de tu
                casa. Que el último envío sea de hace un rato es normal.
              </Body>
            </Card>
          </AnimatedView>
        </>
      )}
    </Screen>
  )
}

/**
 * Un dato del equipo, del tamaño de un widget.
 *
 * Cuadrado —`aspectRatio: 1` sobre el ancho que le tocó de la grilla— y con el
 * ícono arriba y el dato abajo: es la forma que ya tienen los widgets del
 * sistema operativo, así que se lee como una pieza de información suelta y no
 * como una fila de una tabla que hay que recorrer entera.
 */
function StatWidget({
  index,
  icon: Icon,
  label,
  value,
  meter,
}: {
  /** Posición en la entrada escalonada de la pantalla. */
  index: number
  icon: LucideIcon
  label: string
  value: string
  /** 0 a 100 para la barra de la batería. Sin esto, el widget no la dibuja. */
  meter?: number | null
}) {
  return (
    <AnimatedView entering={enterAt(index)} style={{ flexBasis: '47%', flexGrow: 1 }}>
      <Card className="items-center justify-center gap-3 p-4" style={{ aspectRatio: 1 }}>
        {/*
          El ícono va grande y centrado, y no chico contra el margen como en
          `ui/InfoRow`: en una fila el ícono acompaña a un texto que ya se está
          leyendo, y en un widget es lo primero que se ve —lo que dice de qué es
          esta card antes de leer nada—.

          Y con el ícono centrado el texto también se centra: con el dato
          alineado a la izquierda debajo de un ícono al medio, la card quedaba
          con dos ejes distintos y se leía como un error de alineación.
        */}
        <View className="size-16 items-center justify-center rounded-full bg-gray-50">
          <Icon size={34} color="#5c6b74" />
        </View>
        <View className="w-full items-center gap-1">
          <Caption className="text-center">{label}</Caption>
          <Body className="text-center text-[19px] leading-[24px] font-semibold">{value}</Body>
        </View>
        {/*
          La barra se apoya en el piso de la card en vez de colgar del dato.
          Sumándola al bloque centrado, el widget de la batería quedaba con el
          ícono 17 pt más arriba que los otros tres de la grilla: el único que
          tiene un elemento de más terminaba siendo el único desalineado.
        */}
        {meter === undefined ? null : (
          <View className="absolute inset-x-4 bottom-4">
            <Meter value={meter} />
          </View>
        )}
      </Card>
    </AnimatedView>
  )
}

/**
 * La foto del chaleco, con la pastilla del estado apoyada en su pie.
 *
 * Reemplaza al círculo con el ícono que había antes. El paciente tiene el
 * equipo puesto: una foto del chaleco real le confirma que está mirando la
 * pantalla del aparato que lleva encima, cosa que un ícono genérico no hace.
 *
 * La pastilla va superpuesta a la foto y no en su propia fila para que la foto
 * no pierda alto, y se cuelga del techo de la tarjeta —a `PILL_GAP` de él— en
 * vez de anclarse al borde de la foto: así sigue quedando sobre el chaleco y
 * fuera de la tarjeta por más que cambie cuánto monta una sobre la otra.
 *
 * Entra con un fundido hacia arriba, el mismo de toda la app: es el elemento
 * más grande de la pantalla y aparecer de golpe se lee como un salto de layout.
 */
function VestPhoto({
  label,
  tone,
  description,
  isLive,
}: {
  label: string
  tone: DeviceTone
  description: string
  /** Grabando: la pastilla late para que se lea como algo que pasa ahora. */
  isLive: boolean
}) {
  return (
    <AnimatedView entering={enterAt(0)}>
      <Image
        source={VEST}
        style={{ width: '100%', height: VEST_HEIGHT }}
        contentFit="contain"
        // Está en el bundle: no hay descarga que atenuar y el fundido de
        // `expo-image` se sumaría al de la entrada, dejando un doble parpadeo.
        transition={0}
        accessible
        accessibilityLabel={`Tu chaleco: ${label}. ${description}`}
        accessibilityIgnoresInvertColors
      />
      {/*
        `self-center` en la pastilla y no sólo `items-center` en la fila: `Badge`
        trae `self-start` propio, y el `alignSelf` de un hijo le gana al
        `alignItems` del padre — la pastilla quedaba pegada al margen izquierdo
        aunque la fila que la contiene ocupara todo el ancho.
      */}
      <View className="absolute inset-x-0" style={{ bottom: CARD_OVERLAP + PILL_GAP }}>
        <Badge label={label} tone={tone} live={isLive} className="self-center" />
      </View>
    </AnimatedView>
  )
}
