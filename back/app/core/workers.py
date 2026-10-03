"""Frontera entre el event loop y el trabajo que lo bloquearía.

`process_batch_task` es una **corrutina** que Starlette `await`ea en el event
loop desde `BackgroundTasks`. Todo lo que corre adentro y no cede el control
congela la API entera: mientras un lote se procesa, ningún request del dashboard
avanza.

Lo único que cruza a este pool es el motor de detección (`analyze_batch`, desde
`processing._persist_ml_analysis`), que es el trabajo CPU-bound grande. La
decodificación (`decode_batch`) y las lecturas de S3 del procesamiento corren en
línea, como en el flujo de ingesta de `main`: con lotes de ~15 s es poco
trabajo por lote. `run_io` queda disponible para I/O bloqueante, sin llamadores
hoy.

Un pool propio y no `asyncio.to_thread`, por dos razones:

1. `to_thread` usa el executor por defecto, el mismo que FastAPI reparte entre
   dependencias y handlers sincrónicos. Saturarlo con minutos de análisis
   congelaría rutas que no tienen nada que ver.
2. `max_workers=1` **serializa** el análisis a propósito. Dos lotes peleando por
   CPU tardan lo mismo en total y el doble en el p50.

Advertencia honesta sobre el GIL: lo que haya de Python puro en el motor lo
retiene, así que moverlo a un hilo no lo hace desaparecer del event loop — lo
trocea en quantums de `sys.setswitchinterval` (5 ms). Lo que sí paraleliza de
verdad son `filtfilt`, `welch` y los matmul de numpy, que **liberan el GIL** en
sus bucles de C.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Final

from app.core.config import settings

_CPU_EXECUTOR: ThreadPoolExecutor | None = None
_LOCK: Final[threading.Lock] = threading.Lock()


def _executor() -> ThreadPoolExecutor:
    """El pool, creado al primer uso y **recreable después de un shutdown**.

    Perezoso y no un global inicializado al importar, por dos razones:

    1. Un proceso que nunca analiza un lote —un worker de solo lectura, un
       comando de CLI— no tiene por qué levantar hilos.
    2. Un `ThreadPoolExecutor` apagado **no se puede reutilizar**: rechaza todo
       envío nuevo con `RuntimeError: cannot schedule new futures after
       shutdown`. Con un global fijo, cualquier escenario donde la app se levante
       y se baje dos veces en el mismo proceso deja el motor muerto para siempre
       en el segundo arranque. Pasa en los tests (un `TestClient` usado como
       contexto corre el lifespan entero) y pasaría en producción con cualquier
       supervisor que recicle la app en proceso.
    """
    global _CPU_EXECUTOR
    with _LOCK:
        if _CPU_EXECUTOR is None:
            _CPU_EXECUTOR = ThreadPoolExecutor(
                max_workers=settings.ml_worker_threads, thread_name_prefix="ecg-cpu"
            )
        return _CPU_EXECUTOR


async def run_cpu[**P, T](fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Corre trabajo CPU-bound en el pool dedicado, sin bloquear el event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor(), functools.partial(fn, *args, **kwargs))


async def run_io[**P, T](fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Igual pero para I/O bloqueante (boto3, que no tiene API async).

    Va al pool por defecto y no al de CPU: son esperas de red, no cómputo, y
    encolarlas detrás de un análisis de cinco segundos no tiene sentido.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


def warmup_ml() -> None:
    """Paga el import de neurokit2/scipy/sklearn al arrancar y no en el primer lote.

    Medido: el import en frío cuesta **63 s** cuando el bytecode no está
    compilado, y ~1 s cuando sí. Como los imports del motor son perezosos —para
    que un proceso que no analiza nada no los pague—, sin este warmup ese costo
    lo paga entero el primer lote que llega, que es justo el paciente que está
    esperando ver su ECG.
    """
    if not settings.ml_enabled:
        return
    import neurokit2  # noqa: F401
    import sklearn.cluster  # noqa: F401
    from scipy import signal, stats  # noqa: F401


def shutdown_workers() -> None:
    """Espera a que termine el análisis en curso. Se llama desde el `lifespan`.

    `cancel_futures=False`: un lote a medio analizar dejaría el banco de
    plantillas del estudio inconsistente con lo que ya se escribió en la base.

    Deja el pool en `None` para que el próximo uso construya uno nuevo: ver
    `_executor`.
    """
    global _CPU_EXECUTOR
    with _LOCK:
        executor, _CPU_EXECUTOR = _CPU_EXECUTOR, None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=False)
