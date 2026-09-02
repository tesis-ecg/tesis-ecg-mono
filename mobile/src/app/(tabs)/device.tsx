import { Image } from "expo-image";
import {
  Barcode,
  Battery,
  Clock,
  CloudUpload,
  Cpu,
  Info,
  RotateCw,
  Wifi,
} from "lucide-react-native";
import type { LucideIcon } from "lucide-react-native";
import { useState } from "react";

import { VestAura } from "@/components/VestAura";
import { VestMisplacedNotice } from "@/components/VestMisplacedNotice";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Meter } from "@/components/ui/Meter";
import { Refresh } from "@/components/ui/Refresh";
import { Screen } from "@/components/ui/Screen";
import { Spinner } from "@/components/ui/Spinner";
import { Body, Caption, Title } from "@/components/ui/typography";
import {
  DEVICE_AURA,
  DEVICE_STATE,
  type DeviceTone,
} from "@/features/patient/deviceMeta";
import { useDevice, useVestMisplaced } from "@/features/patient/hooks";
import { formatDateTime, formatRelativeTime } from "@/lib/format";
import type { VestAuraTone } from "@/lib/gradients";
import * as haptics from "@/lib/haptics";
import { enterAt, usePressScale, useSpin } from "@/lib/motion";
import { AnimatedPressable, AnimatedView, View } from "@/tw";

const VEST = require("@/assets/images/chaleco.png");

/**
 * Alto de la foto del chaleco.
 *
 * Fijo y con `contain`: el ancho de la pantalla cambia entre un iPhone SE y un
 * Max, y dejando que la foto se escale por ancho el chaleco crecía hasta
 * empujar el resto de la pantalla fuera de la vista en los equipos grandes.
 */
const VEST_HEIGHT = 260;

/**
 * Cuánto monta la tarjeta de estado sobre el pie de la foto.
 *
 * El número parece grande porque no todo es chaleco: el PNG trae ~4 % de alto
 * transparente abajo, que con `VEST_HEIGHT` son unos 11 pt de aire dentro del
 * propio contenedor. Los primeros 11 pt de solape no tapan nada, así que lo que
 * la tarjeta se come de chaleco es la diferencia.
 */
const CARD_OVERLAP = 32;

/** Aire entre la pastilla del estado y el techo de la tarjeta. */
const PILL_GAP = 8;

/** Separación entre los widgets de la grilla, en las dos direcciones. */
const GRID_GAP = 12;

/**
 * Piso de duración de un refresh, para que se llegue a ver que pasó algo.
 *
 * `GET /mobile/device` contesta en decenas de milisegundos. Sin este piso el
 * indicador se dibuja un frame o dos y desaparece: el paciente toca el botón,
 * no ve nada moverse y vuelve a tocarlo. Que la respuesta ya haya llegado no
 * ayuda si nadie se enteró de que salió.
 */
const MIN_REFRESH_MS = 700;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function Device() {
  const device = useDevice();
  const isVestMisplaced = useVestMisplaced();
  /*
    Dos estados para una sola operación, y no por gusto: cada uno alimenta a un
    indicador distinto y los dos no pueden encenderse juntos.

    El `RefreshControl` puesto en `true` por código no es un indicador confiable:
    en iOS corre el contenido hacia abajo sin llegar a dibujar el spinner, y en
    Android apenas asoma un punto. Es un componente pensado para acompañar al
    gesto, no para reportar una carga que empezó en otro lado. Así que el gesto
    se queda con él (`isRefreshing`) y el botón tiene el suyo (`isReloading`),
    que además evita ese salto del contenido al tocarlo.
  */
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isReloading, setIsReloading] = useState(false);
  const reloadPress = usePressScale();
  const reloadSpin = useSpin(isReloading);

  const refresh = async () => {
    // En paralelo y no en serie: el piso corre junto a la consulta, así que un
    // backend lento no suma los dos tiempos.
    await Promise.all([device.refetch(), wait(MIN_REFRESH_MS)]);
    haptics.tap();
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await refresh();
    setIsRefreshing(false);
  };

  const handleReload = async () => {
    setIsReloading(true);
    await refresh();
    setIsReloading(false);
  };

  const state = device.data?.state ?? "none";
  const meta = DEVICE_STATE[state];
  /*
    La mala colocación le gana a cualquier estado del equipo, y por eso se
    resuelve acá y no dentro del mapa: el chaleco puede estar grabando y
    transmitiendo perfecto, y no registrar nada igual porque no hace contacto
    con la piel. Un halo verde detrás de un cartel rojo es la única combinación
    que esta pantalla no puede mostrar.
  */
  const auraTone: VestAuraTone = isVestMisplaced
    ? "alert"
    : DEVICE_AURA[state];

  /*
    El botón de actualizar existe porque esta es la pantalla que el paciente
    abre justamente para confirmar que el equipo está andando, y el gesto de
    arrastrar no se descubre solo: no deja ninguna marca en la pantalla. La
    consulta se refresca sola cada 2 minutos, pero "esperá dos minutos" no es
    una respuesta cuando alguien acaba de acomodarse el chaleco y quiere ver si
    ya está bien.

    Mientras carga gira su propio ícono. Es el indicador que le falta al
    `RefreshControl` disparado por código, y de paso es el que corresponde: la
    respuesta aparece en el mismo lugar que se tocó, no a treinta puntos de
    distancia.

    Sin háptica al tocar: `refresh` ya vibra al terminar, que es lo mismo que se
    siente al arrastrar. Dos vibraciones para una sola acción se leen como que
    pasó algo raro. El toque lo acusa la animación de escala.
  */
  const header = (
    <View className="flex-row items-center justify-between gap-4">
      <Title className="flex-1">Dispositivo</Title>
      <AnimatedPressable
        accessibilityRole="button"
        accessibilityLabel="Actualizar el estado del chaleco"
        accessibilityState={{ busy: isReloading }}
        disabled={isReloading}
        onPress={() => void handleReload()}
        onPressIn={reloadPress.onPressIn}
        onPressOut={reloadPress.onPressOut}
        style={reloadPress.style}
        className="size-12 items-center justify-center rounded-full bg-white shadow-lg"
      >
        {/*
          El giro va en una `View` interna y no en el propio `AnimatedPressable`:
          ese nodo ya lleva el hundimiento del toque, y las dos transformaciones
          en el mismo `style` animado se pisan —queda el `scale` o queda el
          `rotate`, según cuál se escriba último—.
        */}
        <AnimatedView style={reloadSpin}>
          <RotateCw size={24} color="#172126" />
        </AnimatedView>
      </AnimatedPressable>
    </View>
  );

  return (
    <Screen
      fixedHeader={header}
      refreshControl={
        <Refresh
          refreshing={isRefreshing}
          onRefresh={() => void handleRefresh()}
        />
      }
    >
      {device.isLoading ? (
        <Spinner label="Consultando tu chaleco…" />
      ) : device.isError && !device.data ? (
        <Card>
          <ErrorState
            error={device.error}
            onRetry={() => void device.refetch()}
          />
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
              isLive={state === "recording"}
              auraTone={auraTone}
            />

            {/*
              La tarjeta monta sobre el pie de la foto. Va después en el árbol,
              que es lo que la deja pintada encima en iOS, y con `zIndex` para
              que en Android pase lo mismo: ahí el orden de dibujo lo decide la
              elevación, no el orden de los hijos.
            */}
            <AnimatedView
              entering={enterAt(1)}
              style={{ marginTop: -CARD_OVERLAP, zIndex: 1 }}
            >
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
                    <Badge
                      label="Chaleco mal colocado"
                      tone="danger"
                      className="shrink-0"
                    />
                  ) : null}
                </View>

                {/*
                  El cartel de la colocación va **arriba** de la descripción del
                  estado y no debajo, al revés que en Inicio, donde la reemplaza.
                  Acá la descripción no sobra: esta es la pantalla del detalle, y
                  en "sin enviar datos" es lo único que le dice al paciente qué
                  revisar. Pero es la colocación la que pide hacer algo ahora, y
                  leída después de "está registrando y enviando los datos" queda
                  como una aclaración al pie de algo que ya sonó bien.
                */}
                {isVestMisplaced ? <VestMisplacedNotice /> : null}

                <Body className="text-gray-700">{meta.description}</Body>
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
                device.data.batteryPercent === null
                  ? "Sin dato"
                  : `${device.data.batteryPercent}%`
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
              label={
                device.data.studyStartedAt ? "Estudio iniciado" : "Estudio"
              }
              value={
                device.data.studyStartedAt
                  ? formatRelativeTime(device.data.studyStartedAt)
                  : "Sin estudio"
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
                Tu estudio arrancó el{" "}
                {formatDateTime(device.data.studyStartedAt)}.
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
                <Body className="font-semibold">
                  {device.data.serial ?? "—"}
                </Body>
                {device.data.model ? (
                  <Caption className="text-gray-500">
                    {device.data.model}
                  </Caption>
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
                Tu chaleco guarda todo lo que registra y lo envía cada una hora
                por el WiFi de tu casa. Que el último envío sea de hace un rato
                es normal.
              </Body>
            </Card>
          </AnimatedView>
        </>
      )}
    </Screen>
  );
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
  index: number;
  icon: LucideIcon;
  label: string;
  value: string;
  /** 0 a 100 para la barra de la batería. Sin esto, el widget no la dibuja. */
  meter?: number | null;
}) {
  return (
    <AnimatedView
      entering={enterAt(index)}
      style={{ flexBasis: "47%", flexGrow: 1 }}
    >
      <Card
        className="items-center justify-center gap-3 p-4"
        style={{ aspectRatio: 1 }}
      >
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
          <Body className="text-center text-[19px] leading-[24px] font-semibold">
            {value}
          </Body>
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
  );
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
  auraTone,
}: {
  label: string;
  tone: DeviceTone;
  description: string;
  /** Grabando: la pastilla late para que se lea como algo que pasa ahora. */
  isLive: boolean;
  /** Color del halo. No siempre coincide con `tone` — ver `DEVICE_AURA`. */
  auraTone: VestAuraTone;
}) {
  return (
    <AnimatedView entering={enterAt(0)}>
      {/*
        El halo va primero en el árbol para que la foto quede pintada encima en
        las dos plataformas, y entra con ella: es el fondo del chaleco, no un
        elemento aparte, así que comparte su `entering` en vez de tener uno
        propio que lo haría aparecer un instante antes o después.

        Respira sólo cuando el halo mismo está en verde y no cuando el equipo
        está grabando: si el chaleco está mal puesto el halo se pone rojo, y un
        rojo que respira tranquilo diría lo contrario de lo que dice el cartel.
      */}
      <VestAura tone={auraTone} live={auraTone === "live"} />
      <Image
        source={VEST}
        style={{ width: "100%", height: VEST_HEIGHT }}
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
      <View
        className="absolute inset-x-0"
        style={{ bottom: CARD_OVERLAP + PILL_GAP }}
      >
        <Badge
          label={label}
          tone={tone}
          live={isLive}
          className="self-center"
        />
      </View>
    </AnimatedView>
  );
}
