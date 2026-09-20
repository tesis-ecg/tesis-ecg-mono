"""Límites de tamaño para cuerpos HTTP que no se pueden bufferizar sin control."""

# El PDF se persiste en PostgreSQL y se genera completamente en el navegador.
# Mantener este valor centralizado evita que el middleware acepte un cuerpo que
# el servicio rechazaría después de materializarlo.
MAX_CLINICAL_REPORT_PDF_BYTES = 20 * 1024 * 1024
