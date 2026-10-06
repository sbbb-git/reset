#!/usr/bin/env python3
"""Clé d'écriture Supabase, avec repli automatique si le secret est périmé.

POURQUOI CE MODULE
Le 2026-10-06, l'audit a trouvé que TOUTES les écritures vers Supabase
répondaient « HTTP 401 — Invalid API key » depuis au moins le 27 septembre :
sync des séances, sync padel IDF, sync padel national. Le secret
SUPABASE_SERVICE_KEY ne correspondait plus à la clé du projet (rotation de
clé côté Supabase). Pendant ce temps le jeton de l'API Management
(SUPABASE_API), lui, fonctionnait.

Chaque script lisait sa propre copie de la clé dans l'environnement ; une
seule rotation suffisait à tout couper, en silence pour le padel puisque son
step de sync est en continue-on-error.

CE QUE FAIT LE MODULE
  1. essaie SUPABASE_SERVICE_KEY ;
  2. si Supabase la rejette (401/403), redemande les clés du projet à l'API
     Management et retient la première clé de service acceptée — clé
     secrète `sb_secret_…` d'abord, puis l'ancienne `service_role` ;
  3. masque la clé obtenue dans les logs GitHub (::add-mask::) et émet un
     ::warning:: qui dit qu'il faut mettre le secret à jour ;
  4. sinon, échoue avec un message qui dit quoi faire.

La clé n'est jamais affichée : seuls son nom et son type le sont.

Usage :  from supabase_auth import URL, entetes
         req = Request(URL + "/rest/v1/table", headers={**entetes(), ...})
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
API_MANAGEMENT = "https://api.supabase.com/v1"
_TABLE_TEST = "/rest/v1/brands?select=key&limit=1"

_cle_retenue = None


class AccesSupabaseImpossible(RuntimeError):
    pass


def entetes_pour(cle):
    """En-têtes PostgREST selon le format de clé.

    Les nouvelles clés `sb_secret_…` ne sont pas des JWT : elles passent dans
    `apikey` seulement, l'Authorization Bearer étant réservé aux JWT. Les
    anciennes clés `service_role` sont des JWT et vont dans les deux.
    """
    if cle.startswith("sb_"):
        return {"apikey": cle}
    return {"apikey": cle, "Authorization": f"Bearer {cle}"}


def _acceptee(cle):
    """True si PostgREST accepte la clé. Seuls 401/403 signifient « refusée »
    — une autre erreur (table absente, délai) n'est pas un problème de clé."""
    req = urllib.request.Request(URL + _TABLE_TEST, headers=entetes_pour(cle))
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True
    except urllib.error.HTTPError as e:
        return e.code not in (401, 403)
    except Exception:  # noqa: BLE001
        return False


def _masquer(cle):
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{cle}", flush=True)


def _ref_projet():
    m = re.match(r"https://([a-z0-9]+)\.supabase\.(co|in)", URL)
    return m.group(1) if m else None


def _cles_via_management():
    """[(libellé, clé), …] des clés de service du projet, la plus récente
    d'abord. Vide si pas de jeton ou si l'API ne répond pas."""
    jeton = (os.environ.get("SUPABASE_ACCESS_TOKEN")
             or os.environ.get("SUPABASE_API") or "")
    ref = _ref_projet()
    if not jeton or not ref:
        return []
    for suffixe in ("?reveal=true", ""):
        req = urllib.request.Request(
            f"{API_MANAGEMENT}/projects/{ref}/api-keys{suffixe}",
            headers={"Authorization": f"Bearer {jeton}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                items = json.loads(r.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 400 and suffixe:
                continue                    # paramètre inconnu : sans lui
            print(f"  API Management : HTTP {e.code} sur la liste des clés",
                  file=sys.stderr)
            return []
        except Exception as e:  # noqa: BLE001
            print(f"  API Management injoignable : {type(e).__name__}",
                  file=sys.stderr)
            return []
    else:
        return []

    secretes, legacy = [], []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        val = it.get("api_key") or it.get("key") or ""
        nom = str(it.get("name") or "")
        typ = str(it.get("type") or "")
        if not val or "•" in val or "*" in val:      # valeur non révélée
            continue
        if typ == "secret":
            secretes.append((f"{nom or 'secret'} (sb_secret)", val))
        elif nom == "service_role":
            legacy.append(("service_role (legacy)", val))
    return secretes + legacy


def cle():
    """Clé d'écriture acceptée par Supabase, résolue une fois par process."""
    global _cle_retenue
    if _cle_retenue:
        return _cle_retenue
    if not URL:
        raise AccesSupabaseImpossible("SUPABASE_URL non défini")

    principale = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if principale and _acceptee(principale):
        _cle_retenue = principale
        return principale

    if principale:
        print("  ⚠️ SUPABASE_SERVICE_KEY rejetée par Supabase (401) — "
              "repli sur l'API Management", file=sys.stderr)
    for libelle, candidate in _cles_via_management():
        _masquer(candidate)
        if _acceptee(candidate):
            print(f"  ✓ clé de repli acceptée : {libelle}", file=sys.stderr)
            if principale:
                print("::warning::SUPABASE_SERVICE_KEY est périmée. Le sync "
                      "fonctionne grâce au repli via SUPABASE_API, mais mets "
                      "à jour le secret (Supabase -> Project Settings -> API "
                      "Keys -> clé secrète).", flush=True)
            _cle_retenue = candidate
            return candidate

    raise AccesSupabaseImpossible(
        "Aucune clé d'écriture acceptée par Supabase. SUPABASE_SERVICE_KEY "
        "est absente ou rejetée, et le repli via l'API Management n'a rien "
        "donné (jeton SUPABASE_API absent, invalide, ou sans clé de service "
        "révélable). À faire : Supabase -> Project Settings -> API Keys, "
        "copier la clé secrète dans le secret GitHub SUPABASE_SERVICE_KEY.")


def entetes():
    return entetes_pour(cle())


if __name__ == "__main__":
    # Diagnostic : dit quelle clé est utilisée, sans jamais l'afficher.
    try:
        c = cle()
        print("accès Supabase OK —",
              "clé principale" if c == os.environ.get("SUPABASE_SERVICE_KEY")
              else "clé de repli via l'API Management")
    except AccesSupabaseImpossible as e:
        print(f"::error::{e}")
        sys.exit(1)
