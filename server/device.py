def verifier_token(token: str) -> dict[str, Any] | None:
    """Valide le JWT utilisateur auprès de /auth/v1/user et joint son profil."""
    token = (token or "").strip()
    if not token:
        return None

    if token.startswith(APPAREIL_PREFIX):
        appareil = lire_appareil(token)
        if not appareil:
            return None
        user_id = str(appareil.get("user_id") or "")
        if not user_id:
            return None
        profil = lire_profil(user_id) or {}
        return {
            "user_id": user_id,
            "email": str(appareil.get("email") or profil.get("email") or ""),
            "nom": str(appareil.get("nom") or profil.get("nom") or ""),
            "tokens_solde": int(profil.get("tokens_solde") or 0),
            "tokens_utilises": int(profil.get("tokens_utilises") or 0),
            "plan": str(profil.get("plan") or "gratuit"),
        }

    try:
        response = requests.get(
            f"{supabase_url()}/auth/v1/user",
            headers={"apikey": service_key(), "Authorization": f"Bearer {token}"},
            timeout=20,
        )
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    try:
        user = response.json()
    except ValueError:
        return None

    user_id = str(user.get("id") or "")
    if not user_id:
        return None
    meta = user.get("user_metadata") or {}
    profil = lire_profil(user_id) or {}
    return {
        "user_id": user_id,
        "email": str(user.get("email") or profil.get("email") or ""),
        "nom": str(profil.get("nom") or meta.get("full_name") or meta.get("name") or ""),
        "tokens_solde": int(profil.get("tokens_solde") or 0),
        "tokens_utilises": int(profil.get("tokens_utilises") or 0),
        "plan": str(profil.get("plan") or "gratuit"),
    }
