# ADR-002: Scopes explícitos y dos roles

## Estado

Aceptado.

## Decisión

Los únicos roles de cuenta son `medico` y `admin`. La autorización interna distingue
`DOCTOR(doctor_id)` de `ADMIN_GLOBAL`; una ausencia accidental de `doctor_id` nunca promueve una
cuenta a acceso global.

## Consecuencias

- Médico accede sólo a recursos propios y recibe 404 para recursos ajenos.
- Admin tiene vista global y debe elegir médico al crear pacientes.
- Roles legacy hacen abortar la migración para requerir una decisión de datos explícita.
