# ADR-004: Manifest versionado y pirámide ECG

## Estado

Aceptado.

## Decisión

Cada estudio define un raw `float32-le`, checksum y niveles min/max precomputados. El endpoint
manifest entrega metadatos y URLs S3 de diez minutos. La vista inicial elige como máximo 20.000
puntos y el endpoint anterior queda temporalmente disponible para objetos menores a 5 MB.

## Consecuencias

- Abrir un estudio largo no materializa decenas de millones de muestras en el browser.
- Ingesta y seeds deben generar checksum y niveles antes de publicar el estudio.
- La verificación de tamaño y SHA-256 detecta objetos incompletos o alterados.
