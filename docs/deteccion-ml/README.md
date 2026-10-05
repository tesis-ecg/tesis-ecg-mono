# Detección de ruido y anomalías en ECG (ML)

Estrategia técnica para el **Semáforo de Calidad de Señal** (Módulo 2) y el **Motor de
Inteligencia Clínica** (Módulo 6).

> **Alcance vigente — definido explícitamente:**
>
> ```
> QRS normal  →  ruido  →  QRS anómalo
> ```
>
> El objetivo es **detectar ruido** y **detectar anomalías**. *No* identificar enfermedades
> concretas. No hay clasificador de FA, ni de tipos de extrasístole, ni etiquetas
> diagnósticas en esta etapa.
>
> **No se usa un LLM.** El problema es procesamiento de series temporales fisiológicas a
> 500 Hz. Todo lo que sigue es procesamiento de señal clásico + modelos chicos,
> mayoritariamente **no supervisados**.

---

## 0. Por qué este recorte es el correcto

No es una versión reducida por falta de tiempo. Es la decisión técnicamente correcta, por
tres razones que conviene dejar escritas para la defensa:

1. **Es el único camino que arranca sin etiquetas.** Hoy no tenemos ni un solo latido
   etiquetado de nuestro propio hardware (§6.1). Un clasificador de FA necesita miles de
   ejemplos anotados; un detector de anomalías necesita cero.
2. **Encaja exactamente con la restricción regulatoria.** El requerimiento dice *"Soporte a
   la Decisión — No Diagnóstico Autónomo"*. Decir **"este segmento es atípico, revíselo"**
   *es* soporte a la decisión. Decir "esto es una fibrilación auricular" es una afirmación
   diagnóstica, con toda la carga de validación que eso implica. El recorte nos deja del
   lado correcto de esa línea por construcción, no por disclaimer.
3. **El ruido es el cuello de botella real.** Con electrodos secos en un chaleco textil, la
   fracción de registro no diagnóstico es el factor que determina si el sistema sirve.
   Cualquier clasificador que corra antes de resolver esto va a estar midiendo artefactos.

---

## 1. Punto de partida real

### Lo que ya existe

| Qué | Dónde | Estado |
|---|---|---|
| Especificación del pipeline | `back/docs/backend/05-pipeline-ml.md` | Escrita, sin implementar |
| Módulos del pipeline | `back/app/ml/*.py` | **5 archivos con un docstring de placeholder cada uno** |
| Modelo `ECGEvent` | `back/app/db/models/ecg_event.py` | Implementado. Enum incluye `NOISE`, `PVC`, `PAUSE`, `OTHER` |
| Modelo `Alert` | `back/app/db/models/alert.py` | Implementado, con `seen_at` / `acknowledged_at` |
| Modelo `ECGBatch` | `back/app/db/models/ecg_batch.py` | Implementado, con `processing_status` |
| Panel de hallazgos (UI) | Linear [TES-25](https://linear.app/tesis-ecg/issue/TES-25) | Backlog, mock-first. Consume `GET /studies/:id/findings`, que **no existe** |

**Estado de dependencias — verificado.** En `back/.venv` ya están instalados
`neurokit2 0.2.13`, `scipy 1.17.1`, `scikit-learn 1.8.0` y `numpy 2.4.6`, pero
`back/pyproject.toml` **solo declara `numpy`**, y encima dentro del grupo opcional `seed`.
El entorno funciona por accidente: en una instalación limpia no se reproduce. Hay que
declarar las cuatro.

### Lo que impone el hardware

De `info del proyecto/02-firmware-holter.md` y `05-bateria-y-datos.md`:

| Parámetro | Valor | Consecuencia |
|---|---|---|
| AFE | ADS1292R, 24 bits | Rango dinámico sobrado; el limitante es el electrodo, no el ADC |
| Frecuencia de muestreo | **500 Hz** | Coincide con PTB-XL. MIT-BIH (360 Hz), NST (360 Hz), CinC2017 (300 Hz) e Icentia11k (250 Hz) hay que remuestrearlos |
| Derivaciones de ECG | **1 canal** (CH2) | Descarta todo modelo de 12 derivaciones. El universo se reduce a *single-lead* |
| Formato del AFE | 72 bits cada 2 ms: 24 estado de electrodos + 24 ECG + 24 impedancia | Los **24 bits de estado son un detector de ruido gratis** — ver §3.1 |
| Batch | 1 hora, Rice-p2, ~1,7 MB | 1,8 M muestras por batch; ~43 M por estudio de 24 h |
| Detección de QRS | Ya corre **en el firmware** (FIR 161 taps, nRF52840) | Los R-peaks del MCU **no viajan** en el payload actual. Ver §8 |
| Electrodos | **Secos, chaleco textil** | La razón por la que el ruido es el problema #1. Ver §6.3 |

---

## 2. El problema, en dos etapas

El orden `QRS normal → ruido → QRS anómalo` no es una lista de tres clases paralelas: es
una **cascada de dos decisiones**, y la segunda solo tiene sentido si la primera pasó.

```
    ventana de 10 s
         │
    ┌────▼─────────────────────────────┐
    │  ETAPA 1 — ¿es señal analizable?  │   ← binaria/ternaria, por ventana
    └────┬─────────────────────────────┘
         │
    ┌────┴────────────────┐
    │                     │
 NO DIAGNÓSTICO      ANALIZABLE
 (semáforo rojo,          │
  se excluye)             │
                    ┌─────▼──────────────────────────────┐
                    │  ETAPA 2 — ¿este latido se parece   │  ← por latido
                    │  a lo normal de ESTE paciente?      │
                    └─────┬──────────────────────────────┘
                          │
                 ┌────────┴────────┐
                 │                 │
            QRS NORMAL        QRS ANÓMALO
                              (score, sin etiqueta
                               diagnóstica)
```

### La dificultad central: ruido y anomalía se parecen

Este es **el** problema técnico de este alcance, y conviene enunciarlo sin rodeos:

> Un artefacto de movimiento y un latido ectópico producen lo mismo desde el punto de vista
> de un detector ingenuo: **un latido que no se parece a la plantilla**.

Si la Etapa 1 deja pasar ruido, la Etapa 2 lo va a reportar como anomalía. Toda la utilidad
del sistema depende de separar esos dos casos. Los discriminadores que sí funcionan:

| Discriminador | Ruido | Latido anómalo real |
|---|---|---|
| **Relación con el ritmo** | Ninguna. Aparece en cualquier fase del ciclo | Prematuro, y seguido de pausa compensatoria |
| **Contenido espectral** | Banda ancha, energía alta > 40 Hz | Energía **más baja** que un QRS normal (el latido ventricular es más *ancho*) |
| **Línea de base** | Alterada alrededor del latido | Limpia; solo cambia la morfología del complejo |
| **Repetibilidad** | **Nunca se repite igual** | **Se repite con morfología casi idéntica** (mismo foco ectópico → misma forma) |
| **Estado de electrodos** | Suele coincidir con lead-off | Estado de electrodos normal |

La cuarta fila es la más potente y la que define la arquitectura: **el ruido es único, la
anomalía es recurrente.** De ahí sale el método de §4.3.

### Lo que queda fuera de este alcance (documentado, no descartado)

| Problema | Estado |
|---|---|
| Clasificación de ritmo (FA, flutter) | **Fuera de alcance.** Requiere etiquetas. Ver §9, Fase 3 |
| Etiquetado diagnóstico del latido (PVC vs aberrancia supraventricular) | **Fuera de alcance.** Se reporta "anómalo", no el tipo |
| Vigilancia de congestión por bioimpedancia | **Fuera de alcance.** No comparte pipeline y necesita semanas de datos longitudinales |
| Bradicardia / taquicardia / pausa | **Dentro**, pero como reglas de umbral sobre RR (§3, Nivel 0). No es "identificar una enfermedad": es reportar que la FC salió de rango, que es una medición |

---

## 3. Etapa 1 — Detección de ruido

Es la prioridad #1 y se ataca en tres capas de costo creciente. Las tres se implementan; no
son alternativas.

### 3.1 Capa A — Estado de electrodos y saturación *(costo cero, sin ML)*

El AFE entrega **24 bits de estado por frame** que hoy no se usan. El ADS1292R señaliza
lead-off por comparador de continua y por inyección de corriente alterna. Antes de calcular
un solo SQI:

- `lead-off` activo → la ventana es **no diagnóstica**, punto. No hay nada que analizar.
- Señal en riel (saturación del ADC) o *flatline* (varianza ≈ 0, electrodo desconectado).
- Amplitud fuera de rango fisiológico.

Esto solo ya se lleva la mayor parte del descarte en un chaleco textil, es determinístico y
es auditable. **Es el primer ticket de ML del proyecto y no requiere ni un dato de
entrenamiento.**

### 3.2 Capa B — Índices de calidad de señal (SQI) *(sin etiquetas)*

`neurokit2` 0.2.13 ya implementa esto. La firma real es:

```python
nk.ecg_quality(ecg_cleaned, rpeaks=None, sampling_rate=1000,
               method="averageQRS", approach=None)
```

Métodos disponibles y qué nos da cada uno:

| `method` | Qué devuelve | Uso para nosotros |
|---|---|---|
| `"zhao2018"` | **String: `"Excellent"` / `"Barely acceptable"` / `"Unacceptable"`** | **Mapea 1:1 al Semáforo de Calidad de tres colores del Módulo 2.** Combina pSQI (potencia en banda QRS 5–15 Hz sobre 5–40 Hz), kSQI (curtosis) y basSQI (potencia de línea de base 0–1 Hz sobre 0–40 Hz). Acepta `approach="simple"` o `"fuzzy"` |
| `"ho2025"` (alias `"ici"`) | Calidad **latido a latido**, 1/0 por intervalo RR | Corre dos detectores de QRS (`primary_detector="unsw"`, `secondary_detector="neurokit"`) y marca el RR como bueno solo si **ambos coinciden**. Es el enfoque bSQI |
| `"templatematch"` (Orphanidou 2015) | Correlación de cada latido con la plantilla promedio | Se reusa en la Etapa 2 (§4.2) |
| `"dissimilarity"` (Sabeti 2019) | Disimilitud de cada latido respecto de la plantilla normalizada | Alternativa a `templatematch` |
| `"averageQRS"` *(default)* | Índice continuo 0–1, distancia al QRS promedio | **Cuidado**: la propia documentación advierte que 1 no significa "bueno" — si la mayoría de las muestras son malas, parecerse al promedio también es malo. **No usarlo como gate** |

**Recomendación concreta: `zhao2018` + `ho2025` combinados.** La razón es específica y hay
que dejarla anotada: la implementación de `zhao2018` en NeuroKit **descartó el índice qSQI**
del paper original (el que mide coincidencia entre detectores de R) y redistribuyó los pesos
a [0,6 / 0,2 / 0,2]. Justamente qSQI es el índice más robusto ante nuestro modo de falla
dominante — pérdida intermitente de contacto. `ho2025` implementa esa idea por separado.
Corriendo los dos se recupera el ensemble completo de Zhao.

Por qué esta capa es la de mejor relación costo/beneficio: **no necesita una sola etiqueta.**
El acuerdo entre dos detectores de QRS independientes es una señal de calidad autogenerada.

### 3.3 Capa C — Clasificador supervisado de ruido *(el único punto donde hay etiquetas gratis)*

Cuando A y B ya estén midiendo, se entrena un GBM (XGBoost / LightGBM / `sklearn`) sobre el
vector de SQIs para afinar las fronteras. **Y acá aparece el atajo importante:**

> **MIT-BIH Noise Stress Test regala las etiquetas.** El dataset se construye inyectando
> ruido ambulatorio real (baseline wander, artefacto de movimiento de electrodo, ruido
> muscular) sobre ECG limpio **a SNR conocido**. O sea: para cada segmento se sabe
> exactamente cuánto ruido tiene. Es un dataset de calidad de señal etiquetado, gratis, y
> **el proyecto ya lo usa** para medir el codec Rice.

Features: los SQIs de la capa B + curtosis, asimetría, tasa de cruces por cero, potencia por
bandas, número de R-peaks detectado por cada detector y su discrepancia, fracción de muestras
en riel, y la varianza de la línea de base.

Salida: 3 clases (`good` / `acceptable` / `unusable`), calibradas para alimentar el semáforo.

---

## 4. Etapa 2 — Detección de anomalías (sin etiquetas)

Solo sobre segmentos que la Etapa 1 marcó como analizables.

### 4.1 La idea

En vez de preguntar *"¿qué arritmia es?"*, se pregunta **"¿esto se parece a lo normal de
*este* paciente?"**. El modelo de normalidad se construye **por paciente y por estudio**, no
a partir de una población. Eso elimina la necesidad de etiquetas y además resuelve la
variabilidad entre sujetos, que en una sola derivación con electrodo seco es enorme.

### 4.2 Plantilla del paciente

1. R-peaks con `nk.ecg_peaks` sobre los segmentos limpios (15 métodos disponibles; usar
   `"neurokit"` como primario y uno de familia distinta — `"pantompkins1985"` o
   `"kalidas2017"` — como secundario, que ya se necesita para el bSQI de §3.2).
2. Extraer una ventana de **±250 ms alrededor de cada R** (±125 muestras a 500 Hz) con
   `nk.ecg_segment`. Resultado: una matriz de latidos.
3. Alinear por el pico R y normalizar en amplitud.
4. **Plantilla = mediana** de los latidos de los primeros minutos limpios (mediana, no media:
   es robusta a que se cuelen ectópicos en la ventana de construcción).
5. Índice por latido: correlación cruzada contra la plantilla — que es exactamente lo que
   hace `nk.ecg_quality(method="templatematch")`, así que no hay que implementarlo.

La plantilla se **recalcula por tramos** (ej. cada hora): la morfología deriva con el cambio
de postura, y una plantilla fija del minuto 0 marcaría medio estudio como anómalo a las 12 h.

### 4.3 El plano que separa ruido de anomalía

Cada latido se ubica en dos ejes, y **ninguno de los dos necesita etiquetas**:

- **Eje X — Prematuridad:** `RR_anterior / RR_medio_local`. < 1 significa que llegó antes de
  tiempo.
- **Eje Y — Disimilitud morfológica:** 1 − correlación con la plantilla.

```
  disimilitud
  morfológica
      ▲
      │   ┌──────────────────┬──────────────────┐
 alta │   │  PROBABLE RUIDO  │  ANOMALÍA FUERTE │
      │   │  (raro pero no   │  (prematuro Y    │
      │   │   prematuro)     │   distinto)      │
      │   ├──────────────────┼──────────────────┤
      │   │     NORMAL       │  ANOMALÍA DE     │
 baja │   │                  │  TIMING          │
      │   └──────────────────┴──────────────────┘
      └────────────────────────────────────────►
           no prematuro        prematuro
```

El cuadrante superior izquierdo —**morfología rara sin relación con el ritmo**— es
principalmente ruido que se le escapó a la Etapa 1. El superior derecho es la anomalía real.
Esta separación cae sola de dos features calculables, sin un solo dato anotado.

### 4.4 Clustering: el discriminador definitivo

El paso que convierte esto de heurística en método. Sobre la matriz de latidos de **todo el
estudio**:

1. Reducir dimensionalidad (PCA / SVD sobre la matriz de latidos, ~5–10 componentes).
2. Clusterizar (DBSCAN sobre distancia de correlación, o jerárquico).

La lectura del resultado es directa y es la fila 4 de la tabla de §2:

| Patrón del cluster | Interpretación |
|---|---|
| Cluster dominante, miles de miembros | Latido normal del paciente → plantilla |
| Cluster chico pero **compacto y recurrente** (ej. 300 latidos con correlación intra-cluster > 0,9) | **Foco ectópico.** Anomalía real, con carga cuantificable |
| **Singletons dispersos**, sin vecinos | **Ruido.** Un artefacto nunca se repite idéntico |

Esto entrega, sin etiquetas, algo clínicamente accionable: *"este estudio tiene 3
morfologías recurrentes; la segunda aparece 412 veces (0,4 % de los latidos)"*. Eso es
exactamente lo que un cardiólogo quiere ver primero, y no afirma ningún diagnóstico.

### 4.5 Alternativas de mayor costo

Documentadas, no priorizadas:

- **Isolation Forest / One-Class SVM** sobre el vector de features por latido. Alternativa
  razonable al umbral sobre correlación; `scikit-learn` ya está instalado.
- **Autoencoder** entrenado con los latidos normales del propio estudio; el error de
  reconstrucción es el score de anomalía. Mayor capacidad, menor explicabilidad, requiere
  más infraestructura. **Fase 3.**

### 4.6 Presupuesto de cómputo

`05-pipeline-ml.md` estima 3–10 s por batch, pero asume 900 k muestras. Un batch real son
**1,8 M** (500 Hz × 3600 s). Además:

- `nk.ecg_delineate(method="dwt")` es órdenes de magnitud más lento que `ecg_peaks`.
  **Con este alcance no hace falta correrlo**: la delineación P-QRS-T solo se necesita para
  diagnóstico morfológico fino, que está fuera de alcance. Es un ahorro grande que sale gratis
  del recorte.
- El clustering es sobre ~3.600 latidos por batch de una hora, con vectores de ~10
  dimensiones tras PCA. Es trivial para `sklearn`.
- Con esas dos cosas, el `BackgroundTasks` de FastAPI del MVP se sostiene. El upgrade a
  Celery ya está previsto para cuando haya varios dispositivos concurrentes.

---

## 5. El pipeline completo

```
batch (1 h, 1,8 M muestras)
   │
   ├─ [1] Decodificación del frame de 72 bits + Rice-p2 → numpy float32
   │
   ├─ [2] ETAPA 1 — GATE DE CALIDAD (ventanas de 10 s)
   │        ├── Capa A: estado de electrodos, saturación, flatline   ← sin ML
   │        ├── Capa B: zhao2018 + ho2025 (SQIs)                     ← sin etiquetas
   │        └── Capa C: GBM sobre SQIs                               ← entrenado con NST
   │             │
   │             └── unusable → ECGEvent(NOISE) + se EXCLUYE del resto
   │                            (alimenta el Semáforo del Módulo 2)
   │
   ├─ [3] R-peaks sobre segmentos analizables (nk.ecg_peaks, 2 detectores)
   │
   ├─ [4] Serie RR  →  reglas Nivel 0: BRADYCARDIA / TACHYCARDIA / PAUSE
   │
   ├─ [5] ETAPA 2 — ANOMALÍA
   │        ├── plantilla del paciente (mediana de latidos limpios, por tramo)
   │        ├── score por latido: prematuridad × disimilitud
   │        └── clustering del estudio → morfologías recurrentes vs singletons
   │
   ├─ [6] AGREGACIÓN EN EPISODIOS   ◄── crítico, ver §5.1
   │
   ├─ [7] ECGEvent (bulk insert)  +  Alert si supera el umbral del paciente
   │
   └─ [8] processing_status = DONE
```

Las etapas [2], [5] y [6] no están en la especificación actual de `05-pipeline-ml.md`.

### 5.1 La unidad de reporte es el episodio, no el latido

Un estudio de 24 h tiene ~100.000 latidos. Un detector con 99 % de especificidad por latido
genera **1.000 hallazgos falsos por día**. Eso no es triage, es ruido con otra cara. Entre
[5] y [7] tiene que haber agregación:

- Latidos/ventanas positivos contiguos → un solo `ECGEvent` con `duration_seconds`.
- **Agregación por cluster:** 400 latidos anómalos de la misma morfología son **un** hallazgo
  (*"morfología recurrente, 400 ocurrencias"*), no 400 alertas.
- Ventana de refractariedad entre eventos del mismo tipo.
- **Tope duro por estudio:** reportar los N latidos/segmentos más anómalos, no todos los que
  superan un umbral. El presupuesto de revisión del médico es finito y hay que tratarlo como
  una restricción de diseño, no como una consecuencia.

---

## 6. Qué necesitamos: los datos

**Sí, hacen falta muestras de estudios. Lo que hay hoy no alcanza ni remotamente.**

### 6.1 Lo que tenemos hoy

`mediciones/` es una captura de banco del ADS1292R:

| Archivo | Contenido |
|---|---|
| `Device 0 Analysis.csv` | Volcado de registros del ADC. `CONFIG1 = 0x02` → confirma **500 SPS**. `RESP1 = 0xEA` → canal de impedancia activo |
| `Device 0 Volts.csv` | ~1.000 muestras de **CH2** en volts |
| `Device 0 Codes.xls.csv` | Las mismas muestras en códigos crudos + `Status Bits` |

**Son ~2 segundos de señal, de un canal, sin paciente y sin ninguna etiqueta.** Sirve para
validar la decodificación del formato del AFE. Nada más.

### 6.2 Datasets públicos — reordenados por este alcance

| Prioridad | Dataset | Fs | Der. | Para qué |
|---|---|---|---|---|
| **1** | **MIT-BIH Noise Stress Test** | 360 Hz | 2 | **El dataset clave.** Ruido ambulatorio real a **SNR conocido** ⇒ etiquetas de calidad gratis para la Capa C. Ya está en el proyecto |
| **2** | **PhysioNet/CinC Challenge 2017** | 300 Hz | **1** | ~8.500 registros de 30 s de un dispositivo de consumo, **con clase `Noisy` explícita**. Lo más parecido a nuestro caso de uso para validar el gate |
| **3** | **MIT-BIH Arrhythmia** | 360 Hz | 2 | ~110 k latidos anotados. **Se usa solo para EVALUAR** la Etapa 2: se colapsan las anotaciones a normal/no-normal y se mide cuántos latidos no-normales caen en el top-N de anomalía. **Nunca se entrena con esas etiquetas** — el detector es no supervisado por diseño |
| **4** | **Icentia11k** | 250 Hz | **1** | Holter ambulatorio de un canal, 11 k pacientes, hasta 2 semanas. El más parecido en *modalidad de uso*. Volumen para validar la tasa de hallazgos en registros largos |
| 5 | **PTB-XL** | **500 Hz** | 12 | Mismo sample rate que nuestro hardware. Solo la derivación II. Útil como banco de latidos normales |
| — | MIT-BIH AFDB | 250 Hz | 2 | Solo si se retoma clasificación de ritmo (fuera de alcance) |
| — | LUDB / QT | varias | varias | Solo si se implementa delineación (fuera de alcance) |

> **Verificar la licencia de cada dataset antes de usarlo en la tesis** y documentar licencia
> y cita en el capítulo de metodología.

**Normalización obligatoria:** remuestrear todo a 500 Hz y normalizar amplitud a la escala en
mV de nuestro AFE. Un modelo entrenado a 360 Hz y aplicado a 500 Hz sin normalizar falla
silenciosamente — y para SQIs espectrales como pSQI/basSQI, que están definidos en bandas de
frecuencia fijas, el error es directo.

### 6.3 Lo que los datasets públicos NO resuelven

Todos vienen de **electrodos húmedos de gel, colocados por un técnico**. Nuestro dispositivo
usa **electrodos secos en chaleco textil, colocado por el paciente**, con fricción de la
prenda. El perfil de ruido es otro: más baseline wander, más artefacto de movimiento,
pérdidas de contacto intermitentes.

**Los datasets públicos alcanzan para entrenar; no alcanzan para validar.** Y este alcance
—donde el producto *es* el detector de ruido— hace que esa brecha sea todavía más crítica
que en el plan anterior.

### 6.4 Datos propios: qué capturar

| Prioridad | Qué | Volumen mínimo | Para qué |
|---|---|---|---|
| **1** | **Registros de calibración**: sujetos sanos con el chaleco real y maniobras provocadas (reposo, caminata, cambio de postura, electrodo deliberadamente flojo, hablar) con **bitácora de tiempos** | 3–5 sujetos × 1–2 h | **La bitácora ES la etiqueta.** Es el dataset de ruido propio, y se consigue en una tarde |
| **2** | **Registros de 24 h** de sujetos sanos | 3–5 sujetos × 24 h | Tasa de hallazgos falsos en población normal, deriva de la plantilla a lo largo del día, y validación del presupuesto de cómputo con volumen real |
| **3** | **Trial del Hospital Austral** | Lo que dé | Los únicos latidos **anómalos reales** de nuestro hardware. Insustituibles |

El item 1 es el de mejor relación esfuerzo/valor de todo el proyecto: una tarde de grabación
con una planilla de tiempos produce exactamente el dataset que necesita el componente que va
primero en el pipeline.

### 6.5 El dashboard *es* la herramienta de etiquetado

La **Gestión y Validación de Hallazgos** (Módulo 4) —el médico valida, corrige o descarta—
produce el formato de etiqueta que el modelo necesita:

```
detector marca anomalía → médico valida/descarta en el dashboard → se persiste
        ▲                                                             │
        └──────── reentrenamiento / recalibración de umbrales ◄────────┘
```

Y con este alcance el ciclo es aún más valioso: cada "descartado" del médico es una etiqueta
de **ruido** que la Etapa 1 no atrapó, que es justamente lo que más falta nos hace.

**Implicancia hoy:** hay que persistir `validation_status`, `validated_by` y `validated_at`
en `ECGEvent` **desde el día uno**, aunque la UI de validación sea de Fase 3. Si no se guarda
desde el principio, se pierden meses de etiquetas gratis.

---

## 7. Validación y métricas

Las métricas cambian respecto de un clasificador supervisado, porque no hay clases que
predecir.

### Etapa 1 — Ruido

| Métrica | Cómo se mide |
|---|---|
| **Sensibilidad / especificidad de ruido** | Contra MIT-BIH NST a SNR conocido y contra la bitácora de los registros propios |
| **% de registro clasificado como no diagnóstico** | Alimenta el Semáforo y el Panel de Métricas de Población. **Es también la métrica de calidad del hardware**: si el chaleco produce 40 % de registro inservible, eso es un hallazgo de la tesis |
| **Señal buena descartada (falso rechazo)** | El error más caro: descartar señal analizable esconde eventos reales. Debe reportarse por separado |

### Etapa 2 — Anomalía

No se puede usar sensibilidad por clase. Las que aplican:

| Métrica | Por qué |
|---|---|
| **Detección de latidos no-normales en el top-N** | Con MIT-BIH: de los latidos anotados como no-normales, ¿qué fracción entra en los N más anómalos que reporta el sistema? Mide utilidad de priorización sin exigir etiquetas de entrenamiento |
| **Carga de revisión: minutos de revisión por cada 24 h de registro** | La métrica que decide si el médico usa el sistema. Un detector que marca el 5 % de 100.000 latidos es inutilizable, tenga la sensibilidad que tenga |
| **PPV sobre hallazgos revisados** | De lo que muestro, ¿qué fracción el médico considera real? |
| **Pureza de clusters** | ¿Los clusters recurrentes agrupan latidos del mismo tipo anotado? Valida el método de §4.4 |

**Accuracy no se reporta.** Con clases desbalanceadas 1:1000 no significa nada.

### Reglas de evaluación innegociables

1. **Split por paciente, nunca por latido ni por ventana.** Latidos del mismo paciente son
   casi copias entre sí; mezclarlos entre train y test infla la métrica masivamente. Es el
   error más común en la literatura de clasificación de ECG.
2. **Comparar siempre contra el baseline.** Para la Etapa 1, el baseline es la Capa A sola
   (estado de electrodos). Para la Etapa 2, el umbral sobre correlación con la plantilla. Un
   modelo que no le gana claramente al baseline no se implementa.
3. **Evaluar sobre registros completos**, no sobre ventanas curadas. La tasa de hallazgos
   falsos solo aparece con horas de señal continua.
4. **No entrenar con las anotaciones de MIT-BIH.** El detector de anomalías es no supervisado
   por diseño; usar esas etiquetas para entrenar y después reportar performance sobre el mismo
   dataset invalida el resultado.

---

## 8. Gaps concretos en el modelo de datos y el código

| # | Gap | Detalle |
|---|---|---|
| 1 | **No hay `event_type` para "anomalía sin diagnóstico"** | El enum tiene `NOISE`, `PVC`, `PAUSE`, `AFIB`, `OTHER`. Con este alcance el detector produce *"latido atípico"*, que no es ninguno de esos. `OTHER` es semánticamente vago. Hace falta un valor explícito (`ANOMALY` / `MORPHOLOGY_ANOMALY`) — si no, la UI no puede distinguir "no sé qué es" de "no encaja en el catálogo" |
| 2 | **`confidence_score` cambia de semántica** | Hoy significa "confianza en que esto es un PVC". Sin clase diagnóstica, lo que hay es un **score de anomalía** (cuán atípico). Son cosas distintas y conviene un campo aparte, o documentar el cambio explícitamente |
| 3 | **No hay tabla de calidad de señal por segmento** | El Semáforo del Módulo 2 necesita persistir intervalos con su nivel. Emularlo con `ECGEvent(NOISE)` mezcla dos conceptos: la calidad es una propiedad **continua de todo el registro**, no un evento puntual |
| 4 | `ECGEvent` no tiene `model_version` | Sin trazabilidad de qué versión generó cada hallazgo, la auditoría del Módulo 7 es imposible y no se pueden comparar versiones |
| 5 | `ECGEvent` no tiene campos de validación médica | Ver §6.5 — sin esto se pierden las etiquetas |
| 6 | `ECGEvent.batch_id` cuelga del batch, no del estudio | **El clustering de §4.4 es por estudio, no por batch.** Un cluster de morfología recurrente abarca las 24 h. El modelo actual no puede representarlo |
| 7 | `back/pyproject.toml` no declara las dependencias de ML | `neurokit2`, `scipy` y `scikit-learn` están en el venv pero **no en `pyproject.toml`**; `numpy` solo en el grupo opcional `seed`. El entorno actual no es reproducible |
| 8 | Los **24 bits de estado de electrodos no se usan** | Es un detector de lead-off gratis en cada frame. Con este alcance es *la primera línea del producto*, no un detalle |
| 9 | `ECGBatch.num_channels` tiene `default=3` | El hardware entrega **1 canal de ECG**; los otros dos campos del frame son estado e impedancia. Alinear o documentar |
| 10 | El payload no transmite los R-peaks del firmware | El nRF52840 **ya detecta QRS** (FIR 161 taps) pero `ECGChunkData` no los incluye. Ver §10 |

---

## 9. Plan por fases

### Fase 1 — Fundaciones y detección de ruido

- Decodificación del frame de 72 bits del ADS1292R, validada contra `mediciones/`.
- `decompression.py` (Rice-p2) con round-trip verificado sin pérdida.
- Declarar `neurokit2`, `scipy`, `scikit-learn`, `numpy` en `pyproject.toml`.
- **Gate de calidad Capa A** (estado de electrodos, saturación, flatline).
- **Gate de calidad Capa B** (`zhao2018` + `ho2025`) → Semáforo de tres estados.
- R-peaks + reglas RR (bradi / taqui / pausa).
- Migraciones de los gaps 1–6.
- `GET /studies/:id/findings` → desbloquea TES-25.

### Fase 2 — Detección de anomalías y validación

- Ingesta y normalización de NST y CinC 2017 a 500 Hz, 1 derivación.
- **Captura de los registros propios de calibración** (§6.4, prioridad 1 y 2).
- **Gate de calidad Capa C** (GBM sobre SQIs), evaluado contra NST y señal propia.
- Plantilla del paciente + score de anomalía (prematuridad × disimilitud).
- Clustering de morfologías por estudio.
- Agregación en episodios y umbrales por paciente.
- Reporte de validación con las métricas de §7.

### Fase 3 — Iteración clínica y extensiones

- UI de validación de hallazgos → captura de etiquetas propias.
- Reentrenamiento y recalibración con datos del trial del Austral.
- *(Extensión)* Clasificación de ritmo / FA, si el trial provee casos etiquetados.
- *(Extensión)* Autoencoder para anomalía; 1D-CNN sobre señal cruda.
- *(Extensión)* Vigilancia de congestión por bioimpedancia.

### Tickets a crear en Linear

Hoy **no existe ningún ticket** que cubra nada de esto.

| Ticket | Fase | Depende de |
|---|---|---|
| Decodificación del frame ADS1292R + descompresión Rice-p2 | 1 | — |
| Declarar dependencias de ML en `pyproject.toml` | 1 | — |
| Migración: calidad de señal, `ANOMALY` en el enum, `model_version`, validación médica, `study_id` en eventos | 1 | — |
| Gate de calidad Capa A (estado de electrodos + saturación) | 1 | decodificación |
| Gate de calidad Capa B (SQIs `zhao2018` + `ho2025`) → Semáforo | 1 | Capa A |
| R-peaks + reglas RR (bradi / taqui / pausa) | 1 | Capa B |
| `GET /studies/:id/findings` | 1 | reglas RR |
| **[TES-25]** Panel lateral de hallazgos (ya existe, Backlog) | 1 | endpoint findings |
| Ingesta y normalización de NST + CinC 2017 a 500 Hz | 2 | — |
| Protocolo y captura de registros propios de calibración | 2 | hardware operativo |
| Gate de calidad Capa C (GBM) + reporte de validación | 2 | NST + registros propios |
| Plantilla del paciente + score de anomalía por latido | 2 | reglas RR |
| Clustering de morfologías por estudio | 2 | score de anomalía |
| Agregación en episodios + `Alert` con umbrales del paciente | 2 | clustering |

---

## 10. Decisiones pendientes

1. **¿Qué escala en mV corresponde al código crudo del AFE?** `05-pipeline-ml.md` lo deja
   como "a definir con Biomédica en Fase 1". Sin ese factor no hay umbral de amplitud
   transferible entre datasets públicos y señal propia. **Bloqueante para la Fase 2.**
2. **¿Cuál es el presupuesto de revisión del médico?** ¿Cuántos hallazgos por estudio de 24 h
   está dispuesto a mirar? Ese número fija el N del top-N (§5.1) y el punto de operación de
   todo el sistema. Hay que acordarlo con el cardiólogo del Austral **antes** de calibrar.
3. **¿Qué se hace con un segmento no diagnóstico?** ¿Se muestra igual en gris, se oculta, se
   reporta como "X % del estudio no evaluable"? Afecta el diseño de la UI y el contrato del
   endpoint de hallazgos.
4. **¿El trial del Austral incluye pacientes con arritmia conocida?** Si es población sana, no
   habrá latidos anómalos propios y la Etapa 2 quedará validada solo contra datasets públicos.
   Es una limitación a declarar explícitamente en la tesis.
5. **¿Se aprovechan los R-peaks del firmware o se recalcula en la nube?** (gap 10). Nota: el
   bSQI de §3.2 necesita **dos** detectores independientes; el del firmware podría ser uno de
   ellos, lo que le da un valor extra que antes no tenía.
6. **¿Se persisten las señales derivadas (RR, calidad, matriz de latidos) o se recalculan?**
   Guardar la serie de RR y los scores por estudio evita reprocesar 43 M muestras cada vez que
   se ajusta un umbral. Con este alcance, donde la calibración de umbrales va a ser iterativa,
   pesa más que antes.

---

## Referencias

- `Requerimientos.md` — Módulos 2, 3, 4 y 6
- `info del proyecto/02-firmware-holter.md` — AFE, formato de 72 bits, 500 Hz
- `info del proyecto/05-bateria-y-datos.md` — compresión Rice-p2, volúmenes, QRS en firmware
- `info del proyecto/04-cloud.md` — contrato del payload de batch
- `back/docs/backend/05-pipeline-ml.md` — especificación previa del pipeline
- `mediciones/` — captura de banco del ADS1292R (~2 s, CH2)
- NeuroKit2 0.2.13 — `neurokit2/ecg/ecg_quality.py` (métodos verificados sobre el venv del proyecto)
- Zhao & Zhang, *SQI-based ECG quality assessment*, 2018 — pSQI / kSQI / basSQI
- Orphanidou et al., *Signal quality indices for wearable ECG/PPG*, 2015 — template matching
- Sabeti et al., 2019 — índice de disimilitud
- PhysioNet — https://physionet.org
- ANSI/AAMI EC57 — protocolo de evaluación de algoritmos de detección
