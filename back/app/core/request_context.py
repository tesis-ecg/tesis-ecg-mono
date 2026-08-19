import ipaddress

from fastapi import Request


def client_ip(request: Request) -> str | None:
    """Devuelve un IP válido y acotado; nunca persiste un header arbitrario."""
    candidates: list[str] = []
    vercel_forwarded = request.headers.get("x-vercel-forwarded-for")
    if vercel_forwarded:
        candidates.extend(part.strip() for part in vercel_forwarded.split(","))
    if request.client:
        candidates.append(request.client.host)

    for candidate in candidates:
        if len(candidate) > 45:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return None
