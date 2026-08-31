"""Contraseñas iniciales de los pacientes.

El médico las lee en pantalla y se las dicta al paciente, que muchas veces es
una persona mayor tipeando en un celular. Por eso el alfabeto excluye los
caracteres que se confunden al leer en voz alta (0/O, 1/l/I) y la longitud es
fija en 8: es el mínimo de la política "Good" de Auth0 y lo más corto que se
puede dictar sin errores.
"""

import secrets

PASSWORD_LENGTH = 8

_LOWER = "abcdefghijkmnpqrstuvwxyz"  # sin 'l' ni 'o'
_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # sin 'I' ni 'O'
_DIGITS = "23456789"  # sin 0 ni 1
_ALPHABET = _LOWER + _UPPER + _DIGITS


def generate_patient_password() -> str:
    """8 caracteres alfanuméricos con al menos una minúscula, una mayúscula y un dígito.

    Las tres clases están garantizadas y no libradas al azar: la política por
    defecto de Auth0 las exige, y un rechazo del Management API a mitad del alta
    del paciente deja la ficha creada y la cuenta no.
    """
    required = [
        secrets.choice(_LOWER),
        secrets.choice(_UPPER),
        secrets.choice(_DIGITS),
    ]
    rest = [secrets.choice(_ALPHABET) for _ in range(PASSWORD_LENGTH - len(required))]
    characters = required + rest
    # Barajado criptográfico: sin esto la primera posición sería siempre una
    # minúscula, la segunda una mayúscula y la tercera un dígito.
    for index in range(len(characters) - 1, 0, -1):
        swap = secrets.randbelow(index + 1)
        characters[index], characters[swap] = characters[swap], characters[index]
    return "".join(characters)
