"""Clé métier d'une séance : jour | heure | salle | cours, normalisés.

POURQUOI
L'audit du 06/10 a trouvé des stores massivement dupliqués :
  le33foch  136 824 entrées pour 2 624 séances réelles (98 %)
  banote     18 042 entrées pour 1 628 séances réelles (91 %)
  cercles_forme 55 513 pour 21 895 (61 %)
Le 33 Foch et Banote identifiaient chaque séance par `data-bw-widget-id`,
l'identifiant de l'élément HTML que Mindbody RENOUVELLE à chaque affichage :
chaque passage du scraper créait une nouvelle entrée pour la même séance.
Le proxy de Cercles de la Forme, lui, publie la même séance sous plusieurs
`eve_id` différents.

Deux séances au même endroit, à la même heure, avec le même cours sont la
même séance. Cette clé ne dépend que de ce qui est stocké, si bien que le
scraper et le script de dédoublonnage la calculent exactement pareil.

La salle doit être qualifiée par club (« Salle 1 République ») pour qu'il n'y
ait pas de collision entre clubs — c'est le cas des trois sources concernées.
Le coach est exclu volontairement : un remplacement ne crée pas une autre
séance.
"""


def _n(s):
    return " ".join(str(s or "").lower().split())


def cle_seance(date, heure, lieu, cours):
    return f"{date}|{heure}|{_n(lieu)}|{_n(cours)}"


def cle_de(record):
    """Clé d'un enregistrement de store déjà rangé."""
    return cle_seance(record.get("date"), record.get("heure"),
                      record.get("lieu"), record.get("cours"))
