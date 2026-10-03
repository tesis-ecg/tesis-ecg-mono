from sqlalchemy.exc import DBAPIError

#: SQLSTATE de los errores de contención de locks: `lock_not_available` (lo que
#: levanta el `lock_timeout`) y `deadlock_detected`. En los dos Postgres ya
#: abortó la transacción, nada quedó escrito y reintentar es seguro.
LOCK_CONTENTION_SQLSTATES = frozenset({"55P03", "40P01"})


def is_lock_contention(error: DBAPIError) -> bool:
    """¿El error es una espera de lock perdida y no una falla real?

    Se mira el SQLSTATE y no la clase: con asyncpg, SQLAlchemy envuelve la
    excepción original en su propio adaptador (`exc.orig` es un
    `sqlalchemy...asyncpg.Error`, la de asyncpg queda en `__cause__`), así que un
    `isinstance(exc.orig, LockNotAvailableError)` no matchea nunca en una base
    real. El adaptador copia el SQLSTATE en `sqlstate`/`pgcode`, y las clases de
    asyncpg lo traen como atributo, así que esto cubre los dos casos.
    """
    orig = getattr(error, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code in LOCK_CONTENTION_SQLSTATES:
            return True
    return False
