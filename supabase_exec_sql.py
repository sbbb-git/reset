#!/usr/bin/env python3
"""Exécute un fichier SQL via l'API Management de Supabase.

POURQUOI CETTE VOIE
psql ne peut pas joindre l'hôte direct db.<ref>.supabase.co depuis GitHub
Actions : il ne résout qu'en IPv6 et les runners n'en ont pas. Le pooler
règle ça, mais encore faut-il retrouver sa chaîne dans l'interface.
L'API Management, elle, est du HTTPS ordinaire :

    POST https://api.supabase.com/v1/projects/<ref>/database/query
    Authorization: Bearer <jeton personnel>
    {"query": "..."}

Il ne faut donc qu'un jeton (supabase.com/dashboard/account/tokens).
L'identifiant du projet est déduit de SUPABASE_URL, déjà en secret.

DÉCOUPAGE EN INSTRUCTIONS
Les instructions sont envoyées UNE PAR UNE, pas le fichier en bloc. Deux
raisons : VACUUM refuse d'être groupé avec d'autres dans une transaction, et
en cas d'échec on sait exactement laquelle a cassé au lieu de deviner.
Le découpage respecte les corps $$...$$ (fonctions plpgsql) et les chaînes,
sinon un CREATE FUNCTION serait tronché à chaque point-virgule interne.

Usage : SQL_FILE=fichier.sql python3 supabase_exec_sql.py
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.supabase.com/v1"
TOKEN = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
SUPA_URL = os.environ.get("SUPABASE_URL", "")
FICHIER = os.environ.get("SQL_FILE", "")


def project_ref():
    """<ref> extrait de https://<ref>.supabase.co."""
    m = re.search(r"https://([a-z0-9]+)\.supabase\.(co|in)", SUPA_URL or "")
    if not m:
        print("::error::SUPABASE_URL absent ou inattendu — impossible d'en "
              "déduire l'identifiant de projet", file=sys.stderr)
        sys.exit(1)
    return m.group(1)


def decouper(sql):
    """Découpe en instructions, en respectant $$...$$ et les chaînes."""
    out, courant, i, n = [], [], 0, len(sql)
    tag = None          # tag de dollar-quoting en cours, ex. $$ ou $x$
    chaine = None       # ' ou " en cours
    while i < n:
        c = sql[i]
        if tag:
            if sql.startswith(tag, i):
                courant.append(tag); i += len(tag); tag = None; continue
        elif chaine:
            if c == chaine:
                chaine = None
        else:
            m = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if m:
                tag = m.group(0)
                courant.append(tag); i += len(tag); continue
            if c in "'\"":
                chaine = c
            elif c == "-" and sql.startswith("--", i):      # commentaire ligne
                j = sql.find("\n", i)
                j = n if j == -1 else j
                courant.append(sql[i:j]); i = j; continue
            elif c == ";":
                out.append("".join(courant)); courant = []; i += 1; continue
        courant.append(c); i += 1
    reste = "".join(courant).strip()
    if reste:
        out.append(reste)
    # on jette ce qui n'est que commentaires ou vide
    net = [s.strip() for s in out
           if re.sub(r"--[^\n]*", "", s).strip()]
    return _recoller_transactions(net)


def _recoller_transactions(instructions):
    """Refusionne les blocs BEGIN … COMMIT en une seule instruction.

    Chaque appel à l'API est sa propre transaction. Envoyer BEGIN seul ouvre
    donc une transaction qui se referme aussitôt, et les instructions
    suivantes deviennent indépendantes : dans levier2b, les deux DROP COLUMN
    cesseraient d'être annulables ensemble. On les renvoie groupés pour que
    la transaction soit réellement atomique côté serveur.
    """
    out, tampon = [], None
    for s in instructions:
        tete = re.sub(r"--[^\n]*", "", s).strip().upper()
        if tampon is None and tete == "BEGIN":
            tampon = [s]
            continue
        if tampon is not None:
            tampon.append(s)
            if tete in ("COMMIT", "END", "ROLLBACK"):
                out.append(";\n".join(tampon))
                tampon = None
            continue
        out.append(s)
    if tampon is not None:                      # BEGIN sans COMMIT : on refuse
        raise ValueError("bloc BEGIN sans COMMIT — fichier SQL incohérent")
    return out


def executer(ref, sql):
    body = json.dumps({"query": sql}).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/projects/{ref}/database/query", data=body,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=900) as r:
        txt = r.read().decode("utf-8", "ignore")
    try:
        return json.loads(txt)
    except ValueError:
        return txt


def apercu(sql, n=90):
    s = re.sub(r"--[^\n]*", "", sql)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] + ("…" if len(s) > n else "")


def main():
    if not TOKEN:
        print("::error::secret SUPABASE_ACCESS_TOKEN absent. En créer un sur "
              "supabase.com/dashboard/account/tokens puis l'ajouter dans les "
              "secrets Actions du dépôt.", file=sys.stderr)
        sys.exit(1)
    if not FICHIER or not os.path.exists(FICHIER):
        print(f"::error::SQL_FILE introuvable : {FICHIER!r}", file=sys.stderr)
        sys.exit(1)

    ref = project_ref()
    instructions = decouper(open(FICHIER, encoding="utf-8").read())
    print(f"{FICHIER} : {len(instructions)} instructions à jouer sur le projet {ref}\n")

    echecs = 0
    for i, sql in enumerate(instructions, 1):
        print(f"[{i}/{len(instructions)}] {apercu(sql)}")
        try:
            res = executer(ref, sql)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:400]
            print(f"   ❌ HTTP {e.code} : {detail}", file=sys.stderr)
            echecs += 1
            # On s'arrête : continuer sur un état à moitié migré est pire.
            print("::error::arrêt à la première erreur — rien n'est joué "
                  "au-delà de cette instruction", file=sys.stderr)
            sys.exit(1)
        except Exception as e:  # noqa: BLE001
            print(f"   ❌ {type(e).__name__} : {e}", file=sys.stderr)
            sys.exit(1)
        if isinstance(res, list) and res:
            for ligne in res[:5]:
                print(f"   → {json.dumps(ligne, ensure_ascii=False)[:200]}")
            if len(res) > 5:
                print(f"   → … {len(res) - 5} lignes de plus")
        else:
            print("   ok")

    print(f"\n{len(instructions)} instructions jouées, {echecs} échec(s).")


if __name__ == "__main__":
    main()
