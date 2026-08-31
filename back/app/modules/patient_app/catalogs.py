"""Catálogos de la bitácora del paciente.

Viven en el backend y se sirven por `GET /mobile/catalogs` en vez de estar
hardcodeados en la app: cambiar una etiqueta o agregar un síntoma no puede
depender de que el paciente actualice el celular desde la store.

Los `value` son los slugs que se guardan en `patient_report`; los `label` son lo
que ve el paciente, en el castellano rioplatense del resto del producto.
"""

from typing import Final

#: Síntomas del "Diario de Eventos" del Módulo 5 de Requerimientos.md.
SYMPTOMS: Final[tuple[tuple[str, str], ...]] = (
    ("palpitaciones", "Palpitaciones"),
    ("dolor_pecho", "Dolor en el pecho"),
    ("falta_aire", "Falta de aire"),
    ("mareo", "Mareo"),
    ("desmayo", "Desmayo o casi desmayo"),
    ("cansancio", "Cansancio inusual"),
    ("sin_sintomas", "No sentí nada"),
    ("otro", "Otro"),
)

ACTIVITIES: Final[tuple[tuple[str, str], ...]] = (
    ("durmiendo", "Durmiendo"),
    ("reposo", "En reposo"),
    ("caminando", "Caminando"),
    ("ejercicio", "Haciendo ejercicio"),
    ("subiendo_escaleras", "Subiendo escaleras"),
    ("comiendo", "Comiendo"),
    ("trabajando", "Trabajando"),
    ("emocion_fuerte", "Con una emoción fuerte"),
    ("otro", "Otra cosa"),
)

#: El valor que exige completar el campo libre correspondiente.
OTHER = "otro"

SYMPTOM_VALUES: Final[frozenset[str]] = frozenset(value for value, _ in SYMPTOMS)
ACTIVITY_VALUES: Final[frozenset[str]] = frozenset(value for value, _ in ACTIVITIES)

#: "No sentí nada" es excluyente: marcarlo junto a "dolor en el pecho" produce
#: un registro que el médico no puede interpretar.
EXCLUSIVE_SYMPTOM = "sin_sintomas"

_SYMPTOM_LABELS: Final[dict[str, str]] = dict(SYMPTOMS)
_ACTIVITY_LABELS: Final[dict[str, str]] = dict(ACTIVITIES)


def _humanize(value: str) -> str:
    readable = value.replace("_", " ").strip()
    return readable[:1].upper() + readable[1:] if readable else value


def symptom_label(value: str) -> str:
    """Etiqueta del catálogo, con degradación elegante.

    El fallback existe por los registros viejos: si un slug se retira del
    catálogo, los que ya se guardaron tienen que seguir siendo legibles para el
    médico y no volverse un `undefined` en pantalla.
    """
    return _SYMPTOM_LABELS.get(value) or _humanize(value)


def activity_label(value: str) -> str:
    return _ACTIVITY_LABELS.get(value) or _humanize(value)
