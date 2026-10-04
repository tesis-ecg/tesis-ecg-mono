from enum import StrEnum
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PREVIEW = "preview"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    s3_bucket_name: str
    s3_endpoint_url: str = ""
    # Endpoint con el que se firman las URLs que consume el navegador. En Docker,
    # `s3_endpoint_url` es `http://minio:9000` — un host que solo resuelve dentro
    # de la red de compose. La firma SigV4 incluye el header `Host`, así que la URL
    # no se puede reescribir después: hay que firmarla directamente contra el host
    # público. Vacío = usar `s3_endpoint_url`.
    s3_public_endpoint_url: str = ""
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = "us-east-1"
    environment: Environment = Environment.DEVELOPMENT

    # Auth0
    auth0_domain: str
    auth0_client_id: str
    auth0_client_secret: str
    auth0_audience: str
    auth0_mgmt_client_id: str
    auth0_mgmt_client_secret: str
    auth0_connection: str = "Username-Password-Authentication"

    # Session JWT (our own, not Auth0's)
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = Field(default=60, ge=5, le=1440)
    jwt_issuer: str = "holter-api"
    jwt_audience: str = "holter-dashboard"
    # Audience propia de la app móvil. Separarla es lo que impide que una cookie
    # de sesión del portal sirva como Bearer en `/mobile` y viceversa: los dos
    # tokens los firma el mismo secreto, y sin esto serían intercambiables.
    jwt_mobile_audience: str = "holter-mobile"
    mobile_access_expire_minutes: int = Field(default=60, ge=5, le=1440)
    # 60 días: el paciente usa la app cada tantos días, no todos los días. Un
    # refresh corto lo obligaría a re-loguearse justo cuando llega el aviso.
    mobile_refresh_expire_days: int = Field(default=60, ge=1, le=365)
    auth_rate_limit_secret: str | None = Field(default=None, min_length=32)
    # Cifra la copia legible de la API key de cada chaleco (`app.core.device_keys`).
    # Si no está, la clave se deriva por HKDF del `jwt_secret`: un secreto nuevo
    # obligatorio rompería todos los .env, el compose y el deploy de una.
    device_api_key_secret: str | None = Field(default=None, min_length=32)
    readiness_token: str | None = Field(default=None, min_length=32)

    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")
    s3_presign_expire_seconds: int = Field(default=600, ge=60, le=3600)

    # Ingesta de tramas del chaleco.
    # 8 MB ≈ 32.700 tramas ≈ 4,8 h de backlog a 1,8 tramas/s. Un equipo que
    # estuvo más tiempo sin conexión tiene que trocear el envío — que es lo que
    # hace igual, porque la flash de a bordo aguanta ~9,9 h.
    ingest_max_batch_bytes: int = Field(default=8 * 1024 * 1024, ge=256, le=64 * 1024 * 1024)
    #: Exigir las cabeceras de sincronización horaria del puente WiFi
    #: (`X-Bridge-Epoch-Ms` y compañía, ver `docs/integracion-ingesta-con-horario.md`).
    #: El contrato dice obligatorias, pero arranca apagado a propósito: el
    #: firmware que hoy está en campo todavía no las manda y prenderlo antes de
    #: que salga su versión lo dejaría sin poder subir señal. Se prende cuando
    #: Biomédica confirma el despliegue del puente.
    ingest_require_time_sync: bool = True
    #: Cuánto puede alejarse `X-Bridge-Epoch-Ms` de nuestra hora antes de que el
    #: ancla se considere basura. Cubre la deriva razonable de un puente que
    #: propaga una sincronización vieja, y descarta el epoch 0 de un SNTP roto.
    ingest_time_sync_max_skew_seconds: int = Field(default=6 * 3600, ge=60, le=7 * 86_400)
    #: Salto de hora de pared entre dos tramas consecutivas que abre un tramo
    #: nuevo en la línea de tiempo. Por debajo de esto es jitter del reloj del
    #: equipo; por encima, el chaleco no estuvo grabando.
    ingest_timeline_gap_tolerance_ms: int = Field(default=2_000, ge=100, le=600_000)

    # Dashboard / watchdog
    #: Sin noticias del equipo por más de esto: **aviso**.
    #:
    #: Una hora son seis ventanas de envío perdidas (el puente despacha cada 10
    #: min), así que es un corte que no dispara por ruido y avisa temprano.
    device_stale_hours: int = Field(default=1, ge=1, le=24)
    #: Sin noticias por más de esto: **crítico, se está por perder registro**.
    #:
    #: El número sale de la autonomía offline MEDIDA sobre esta placa, no de la
    #: documentada. La flash aguanta 9,94 h con la señal de PhysioNet, pero sobre
    #: el equipo real el ratio de compresión depende de cuánta interferencia de
    #: red entra, y eso depende de cómo quede puesto el chaleco: 5,1 h con el
    #: chaleco flojo, 7,1 con gel, 8,6 bien puesto (`INTEGRACION.md` §9.1).
    #:
    #: **Se dimensiona contra las 5,1 h**, que es el caso normal de un paciente
    #: durante 15 días, no el extremo. Cuatro horas dejan ~1 h de margen para
    #: intervenir antes de que el log circular empiece a pisar señal sin subir.
    #: El valor anterior era 10 h, o sea que el sistema avisaba DESPUÉS de que ya
    #: se había perdido registro.
    device_critical_hours: int = Field(default=4, ge=1, le=24)
    dashboard_low_battery_pct: int = 45
    #: Ventana de silencio del aviso de falla grave del equipo. Los bits 2, 4 y 6
    #: de `statusFlags` son ESTADOS: el equipo los repite mientras la condición
    #: esté, así que sin esto una flash rota alertaría en cada lote.
    device_fault_debounce_minutes: int = Field(default=60, ge=1, le=1440)
    # Los dos límites alimentan el `default` de un Query(ge=1, le=50), y FastAPI no
    # valida el default: las cotas tienen que estar acá o un .env fuera de rango
    # pasaría sin chistar cuando el FE llama sin query params.
    dashboard_widget_limit: int = Field(default=4, ge=1, le=50)
    dashboard_alerts_limit: int = Field(default=8, ge=1, le=50)

    # Push (Expo). Apagado por defecto: los tests y CI no salen a internet, y
    # con esto en falso el sender es un noop que igual registra qué se habría
    # mandado.
    expo_push_enabled: bool = False
    expo_push_url: str = "https://exp.host/--/api/v2/push/send"
    #: Solo hace falta si el proyecto de Expo tiene push security habilitado.
    expo_access_token: str | None = None
    #: Ventana de silencio por equipo para el aviso de mala colocación. Sin
    #: esto, un chaleco que rebota le manda quince notificaciones al paciente
    #: mientras se lo acomoda.
    vest_status_debounce_minutes: int = Field(default=30, ge=1, le=1440)

    # --- Motor de detección (app/ml) ---------------------------------------- #
    # Todo umbral del pipeline vive acá y no hardcodeado adentro: son parámetros
    # clínicos que se van a recalibrar contra MIT-BIH y contra el chaleco real,
    # y recompilar la imagen para mover un umbral no es una opción.
    ml_enabled: bool = True
    #: Pool propio para el cómputo pesado. En 1 el análisis queda serializado, que
    #: es lo que se quiere: dos lotes peleando por CPU tardan lo mismo en total y
    #: el doble en el p50.
    ml_worker_threads: int = Field(default=1, ge=1, le=8)
    #: Bloque de análisis. Los lotes del chaleco son de ~15 s y una taquicardia
    #: necesita 30 s sostenidos (`ml_rhythm_min_seconds`): el motor no corre por
    #: lote sino por bloques de la corrida, detrás de un cursor
    #: (`processing.append_ml_analysis`). Tiene que ser múltiplo de la ventana
    #: de calidad para que las ventanas de un bloque no queden cortas.
    ml_analysis_block_seconds: float = Field(default=300.0, ge=30.0, le=3600.0)
    #: Señal ya analizada que se le antepone a cada bloque. Es lo que deja ver
    #: un R-R, una pausa o una taquicardia que cruzan el borde; de esa parte no
    #: se vuelve a informar nada. No puede superar al bloque, y no puede ser
    #: más corta que `ml_rhythm_min_seconds`: una taquicardia de 33 s que cruza
    #: el borde a los 25 s no la veía entera ningún bloque.
    ml_analysis_context_seconds: float = Field(default=60.0, ge=0.0, le=3600.0)
    #: Señal del bloque **siguiente** que se lee después de cada uno, para que
    #: el final del bloque no sea un borde duro (`pipeline.analyze_batch`). De
    #: ahí no se informa nada; lo que cuesta es que el bloque espera a que la
    #: corrida lo pase por este tanto. 30 s cubren los dieciséis latidos hacia
    #: adelante de la mediana de prematuridad (`hrv.LOCAL_WINDOW_BEATS`) hasta
    #: 32 lpm, y de sobra el R que el detector pierde en el borde y el
    #: asentamiento del notch. La cola de una corrida cerrada no tiene: después
    #: no hay señal.
    ml_analysis_lookahead_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    #: Bloques por pasada con la fila del estudio tomada. Un estudio atrasado
    #: —el motor se volvió a prender— se pone al día de a esta cantidad por
    #: lote, en vez de colgar la ingesta de ese estudio. Cada bloque son ~50 GET
    #: de S3 (crudo y flags de ~26 lotes) más el análisis, ~1 s: la pasada
    #: además no arranca un bloque que la llevaría más allá de 1,5 s
    #: (`processing.ML_PASS_BUDGET_SECONDS`), debajo del `lock_timeout` de 3 s
    #: con que la ingesta del lote siguiente espera la fila. En régimen es un
    #: bloque cada ~20 lotes y el tope no se toca.
    ml_analysis_max_blocks_per_pass: int = Field(default=4, ge=1, le=288)
    #: Minutos sin lotes nuevos después de los cuales la cola de la corrida
    #: abierta se analiza igual (`processing.flush_stale_tails`). 15 = vez y
    #: media el ciclo de subida de 10 min: un ciclo normal no la dispara. El
    #: precio: si la corrida después sigue, sus bloques arrancan donde quedó el
    #: cursor y no en un múltiplo del bloque, así que dónde caen los bordes
    #: depende de cuándo dejó de subir. Y ese borde se analizó sin contexto
    #: derecho —no había señal—: los hallazgos se empalman igual, pero ahí
    #: vuelve lo que un borde duro cambia (un R perdido o fantasma en los
    #: totales, la frecuencia extrema de un episodio). 0 lo apaga.
    ml_open_tail_flush_minutes: float = Field(default=15.0, ge=0.0, le=1440.0)
    #: Antigüedad máxima, en hora de pared, de un hallazgo del motor que le
    #: manda un push al paciente. Más viejo, se escribe igual —evento y alerta
    #: para el médico— pero no despierta a nadie: avisar ahora de una pausa de
    #: hace horas (el backlog de un día sin WiFi, el motor que se volvió a
    #: prender y se pone al día) no le sirve al paciente para nada. 60 cubre la
    #: cadena normal —ciclo de subida de 10 min, bloque de 5, cola vieja a los
    #: 15— con margen. 0 lo apaga: avisa todo.
    ml_push_max_age_minutes: float = Field(default=60.0, ge=0.0, le=10080.0)

    #: Ventana del gate de calidad. 10 s es el estándar de la literatura de SQI
    #: (Zhao 2018) y entra ~10 latidos, suficiente para que el bSQI tenga sentido.
    ml_quality_window_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    #: pSQI = potencia 5-15 Hz / 5-40 Hz. Un QRS concentra ahí su energía.
    ml_quality_psqi_min: float = Field(default=0.50, ge=0.0, le=1.0)
    #: kSQI = curtosis. Una señal con QRS es leptocúrtica; el ruido gaussiano da ~3.
    ml_quality_ksqi_min: float = Field(default=5.0, ge=0.0, le=100.0)
    #: basSQI = 1 - potencia 0-0,5 Hz / 0-40 Hz. Cae con la deriva de línea de
    #: base. La banda es 0-0,5 y no los 0-1 del paper: a 60 lpm el fundamental
    #: cardíaco cae en 1 Hz y el propio ritmo contaría como deriva.
    ml_quality_bassqi_min: float = Field(default=0.90, ge=0.0, le=1.0)
    #: bSQI = acuerdo entre el detector de R del firmware y el de la nube. Es el
    #: índice que NeuroKit descartó de zhao2018 y sin el cual el gate aprueba
    #: ruido gaussiano puro como `Excellent` (medido).
    ml_quality_bsqi_min: float = Field(default=0.80, ge=0.0, le=1.0)
    #: `FLAG_R_PEAK` **no cae sobre el pico**: cae sobre la muestra en la que el
    #: detector del MCU confirma el latido, 200-300 ms después (FIR de 161 taps =
    #: 160 ms de retardo de grupo, + 40 ms de la cascada de detección, + hasta
    #: 100 ms de ventana de confirmación). Medido por el equipo de firmware sobre
    #: el chaleco el 2026-09-03. Sin compensarlo el bSQI da ~0 contra la
    #: tolerancia de ±150 ms, **todas las ventanas quedan `marginal` y no queda
    #: una sola muestra analizable**: el motor entero enmudece sin un solo error.
    ml_firmware_peak_lag_ms: float = Field(default=250.0, ge=0.0, le=1000.0)
    #: Refractario propio sobre los picos del firmware. Su detector queda ciego
    #: 200 ms y vuelve a confirmar sobre la cola del mismo complejo: 31 de 110
    #: intervalos por debajo de 300 ms en las capturas del chaleco. Se aplica
    #: solo al tren que alimenta el bSQI, nunca al que produce los hallazgos de
    #: ritmo, así que no puede esconder una taquicardia.
    ml_firmware_peak_refractory_ms: float = Field(default=300.0, ge=0.0, le=1000.0)
    #: Amplitud pico a pico por debajo de la cual la ventana es una línea plana.
    ml_flatline_uv: float = Field(default=20.0, ge=1.0, le=1000.0)
    #: Red eléctrica que se quita (notch en la fundamental y sus armónicas) antes
    #: de los índices espectrales. 50 Hz en Argentina; 0 apaga la remoción. Sin
    #: esto ~2 mV pico a pico de red sobre un ECG perfectamente visible bajan la
    #: curtosis por debajo del umbral: medido en el chaleco, de 0 a 26 ventanas
    #: buenas de 29 (`tools/vest/evaluate.py`). Solo 0 o 45-65 Hz: ver
    #: `_mains_is_a_grid`.
    ml_mains_hz: float = Field(default=50.0, ge=0.0, le=65.0)

    #: 50 y no 60: la bradicardia sinusal nocturna a 55 lpm es normal en un adulto
    #: sano, y con 60 se marcaría media noche de todos los Holter.
    ml_bradycardia_bpm: float = Field(default=50.0, ge=20.0, le=60.0)
    ml_tachycardia_bpm: float = Field(default=100.0, ge=60.0, le=220.0)
    #: `Requerimientos.md` §6.B pide R-R > 2000 ms; 2,5 s deja margen sobre una
    #: extrasístole con pausa compensatoria, que no es una pausa patológica.
    ml_pause_seconds: float = Field(default=2.5, ge=1.5, le=10.0)
    #: Un episodio de ritmo tiene que sostenerse para ser un hallazgo y no un
    #: artefacto de dos latidos mal detectados.
    ml_rhythm_min_seconds: float = Field(default=30.0, ge=5.0, le=300.0)

    #: Correlación mínima para que un latido entre en una plantilla existente.
    ml_template_match_threshold: float = Field(default=0.90, ge=0.5, le=0.999)
    #: Correlación por encima de la cual dos plantillas se funden al cerrar.
    ml_template_merge_threshold: float = Field(default=0.95, ge=0.5, le=0.999)
    ml_template_max: int = Field(default=40, ge=4, le=256)
    #: Miembros a partir de los cuales una plantilla deja de ser ruido disperso y
    #: pasa a ser un foco recurrente. Es el discriminador central del método.
    ml_recurrent_cluster_min_beats: int = Field(default=30, ge=2, le=10_000)
    ml_anomaly_score_min: float = Field(default=0.35, ge=0.0, le=1.0)

    #: El hueco entre latidos anómalos se mide en LATIDOS y no en segundos: un
    #: bigeminismo alterna normal/ectópico, y un umbral en segundos lo partiría en
    #: veinte hallazgos a 100 lpm y en uno solo a 50.
    ml_episode_gap_beats: int = Field(default=3, ge=0, le=50)
    #: 2 y no 1: con 1 la regla "un latido aislado que no pertenece a ningún foco
    #: conocido es ruido" nunca se aplica, porque todo grupo tiene al menos un
    #: miembro. Con 2, un singleton solo sobrevive si su cluster ya es recurrente
    #: — que es exactamente el discriminador del método.
    ml_episode_min_beats: int = Field(default=2, ge=1, le=100)
    ml_episode_refractory_seconds: float = Field(default=10.0, ge=0.0, le=300.0)
    #: Presupuesto de revisión del médico. 200 hallazgos ≈ 30-40 min de lectura.
    #: Sin tope, 99 % de especificidad por latido son ~1.000 falsos por día.
    ml_findings_max_per_study: int = Field(default=200, ge=10, le=5000)
    ml_findings_max_per_kind: int = Field(default=50, ge=5, le=1000)
    #: Mide QT, QTc de Fridericia y amplitud R en cada bloque analizado
    #: (`app/ml/intervals.py`) y los guarda en `ecg_interval_measurement`, una
    #: fila por bloque. **Dato de investigación y nada más**: ninguna API, ni el
    #: informe, ni el visor, ni un hallazgo los leen, porque la validación contra
    #: la QT Database dejó un QTc que casi no sigue al del cardiólogo y que no
    #: puede ver un QT largo (`tools/physionet/README.md`). Se exportan para la
    #: tesis con `app.scripts.export_interval_measurements`. Cuesta ~0,03 s por
    #: bloque de 5 min; en falso no se calcula nada.
    ml_interval_measurements_enabled: bool = True

    @property
    def is_secure_environment(self) -> bool:
        return self.environment in {Environment.PREVIEW, Environment.PRODUCTION}

    @property
    def rate_limit_secret(self) -> str:
        return self.auth_rate_limit_secret or self.jwt_secret

    @field_validator("ml_mains_hz")
    @classmethod
    def _mains_is_a_grid(cls, value: float) -> float:
        # Entre 0 y 45 no hay ninguna red: con 0,5 Hz el notch se come el
        # fundamental cardíaco y con 25 cae adentro de la banda de los índices.
        # Un valor ínfimo (1e-6) haría que el bucle de armónicas no termine.
        if 0.0 < value < 45.0:
            raise ValueError("ML_MAINS_HZ tiene que ser 0 (apagado) o una red de 45-65 Hz")
        return value

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.is_secure_environment and self.frontend_url.scheme != "https":
            raise ValueError("FRONTEND_URL debe usar HTTPS fuera de development/test")
        if self.environment == Environment.PRODUCTION and self.s3_endpoint_url.startswith(
            "http://"
        ):
            raise ValueError("S3_ENDPOINT_URL no puede usar HTTP en producción")
        if self.is_secure_environment and (
            len(set(self.jwt_secret)) < 8
            or self.jwt_secret.lower() in {"change-me", "changeme", "secret"}
        ):
            raise ValueError("JWT_SECRET es demasiado predecible para preview/producción")
        if self.is_secure_environment and not self.readiness_token:
            raise ValueError("READINESS_TOKEN es obligatorio en preview/producción")
        # Los dos umbrales del watchdog son escalones de la misma escala: si el
        # crítico no queda por encima del aviso, el equipo salta a crítico sin
        # pasar por el aviso y el escalón temprano deja de existir.
        if self.device_critical_hours <= self.device_stale_hours:
            raise ValueError("DEVICE_CRITICAL_HOURS tiene que ser mayor que DEVICE_STALE_HOURS")
        # El contexto es señal del bloque anterior: más largo que el bloque
        # releería señal que ya no es del anterior sino de dos bloques atrás.
        if self.ml_analysis_context_seconds > self.ml_analysis_block_seconds:
            raise ValueError("ML_ANALYSIS_CONTEXT_SECONDS no puede superar al bloque")
        if self.ml_analysis_lookahead_seconds > self.ml_analysis_block_seconds:
            raise ValueError("ML_ANALYSIS_LOOKAHEAD_SECONDS no puede superar al bloque")
        # Un episodio sostenido que cruza un borde lo tiene que ver entero algún
        # bloque. Con el contexto más corto que la duración mínima, uno de 33 s
        # que el borde parte en 25 + 8 no llega al mínimo de ningún lado.
        if self.ml_analysis_context_seconds < self.ml_rhythm_min_seconds:
            raise ValueError(
                "ML_ANALYSIS_CONTEXT_SECONDS no puede ser menor que ML_RHYTHM_MIN_SECONDS: "
                "una taquicardia o bradicardia sostenida que cruza el borde de un bloque "
                "no la vería entera ningún bloque"
            )
        # Con un bloque que no es múltiplo de la ventana, la última ventana de
        # cada bloque absorbe un resto y la grilla de calidad deja de ser la
        # misma según dónde cayó el borde.
        windows = self.ml_analysis_block_seconds / self.ml_quality_window_seconds
        if abs(windows - round(windows)) > 1e-9:
            raise ValueError(
                "ML_ANALYSIS_BLOCK_SECONDS tiene que ser múltiplo de ML_QUALITY_WINDOW_SECONDS"
            )
        return self


# `BaseSettings` values are provided from environment variables at runtime.
settings = Settings()  # type: ignore[call-arg]
