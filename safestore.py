#!/usr/bin/env python3
"""Stockage sécurisé partagé par tous les scrapers — anti-perte de données.

Garanties :
1. ÉCRITURE ATOMIQUE : on écrit dans un fichier temporaire puis os.replace()
   -> jamais de JSON à moitié écrit (donc jamais corrompu par une interruption).
2. ABANDON SI CORROMPU : si le fichier existant est illisible, on LÈVE une
   erreur au lieu de repartir de zéro -> on n'écrase JAMAIS un bon fichier par
   du vide (la dernière bonne version reste dans git).
3. GARDE ANTI-RÉTRÉCISSEMENT : les stores ne font que grossir (on accumule, on
   ne supprime jamais une séance passée). Si une sauvegarde contient MOINS
   d'entrées que le fichier sur disque, on refuse d'écrire -> impossible
   d'effacer accidentellement l'historique déjà acquis.

Filet ultime : chaque sauvegarde est committée dans git -> historique immuable,
toute version passée reste récupérable même si le fichier vivant est touché.
"""
import json
import os
import tempfile


def load(path):
    """Charge le store. Fichier absent -> {}. Fichier corrompu -> ERREUR (on
    n'écrase pas un bon fichier par du vide)."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)  # JSONDecodeError volontairement non rattrapé -> abandon
    if not isinstance(data, dict):
        raise ValueError(f"{path}: format inattendu (pas un dict)")
    return data


def save(store, path, allow_shrink=False):
    """Écrit le store de façon atomique, en refusant tout rétrécissement."""
    if not isinstance(store, dict):
        raise ValueError("save() attend un dict")
    if os.path.exists(path) and not allow_shrink:
        try:
            with open(path, encoding="utf-8") as f:
                current = json.load(f)
            if isinstance(current, dict) and len(store) < len(current):
                raise RuntimeError(
                    f"REFUS d'écrire {path} : {len(store)} entrées < {len(current)} "
                    f"déjà enregistrées (perte de données évitée).")
        except (ValueError, OSError):
            pass  # fichier existant illisible : on laisse passer (on le remplace par du bon)
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomique
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
