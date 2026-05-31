# Padel IDF — état & suggestions d'améliorations

## État actuel (mai-juin 2026)

### Couverture
| Plateforme | Clubs IDF | Sessions live | Historique |
|---|---:|---:|---|
| Anybuddy | 62 | ~26k | ❌ pas accessible |
| UrbanPadel | 4 | ~2k | ❌ |
| Doinsport | 54 | ~1.7k | ✅ jusqu'à 2-3 ans (backfill ~60-100k+ bookings) |
| Playtomic | 23 | ~9k | ❌ |
| **Total** | **~130 uniques (avec 19 clusters multi-plateformes)** | ~40k | ~80k+ via Doinsport |

### Stack technique
- 4 scrapers Python (parallèle x8) + workflow GitHub Action /30 min
- Store JSON (`padel_idf_data.json`) + Supabase (`padel_clubs`, `padel_slots`)
- Mapping cross-plateforme (`padel_club_unified.json`)
- Dashboard `padel_idf.html` (KPIs, classement, heatmap, table)
- Carte `padel_map.html` (Leaflet, markers couleur par plateforme)
- Historique Doinsport sur `padel_idf_history.json`

## Améliorations prioritaires

### 1. Cross-validation des prix entre plateformes
**Problème** : un même club peut avoir des prix différents selon le canal (Aldea Padel : 45€/60min sur Anybuddy, prix variables sur Doinsport).
**Solution** : pour chaque cluster `unif_*`, comparer la médiane des prix par durée. Si écart > 20% → flagger comme "discrimination par canal" (utile pour l'analyse commerciale).
**Effort** : moyen, déjà partiellement implémenté dans le dashboard.

### 2. Détection d'anomalies live
**Problème** : actuellement on ne sait pas si un club ferme ses portes, change ses prix de 50%, ou si une plateforme tombe en rade.
**Solution** : 
- Alerte si un club n'a renvoyé aucune donnée depuis 24h (probable fermeture ou changement de plateforme)
- Alerte si le prix moyen d'un club bouge de > 30% en 7 jours (changement tarifaire significatif)
- Alerte si une plateforme renvoie 0 résultat (panne API)
**Effort** : faible — un script Python cron + notification (webhook Slack/mail).

### 3. Backfill historique Doinsport en routine
**Idée** : actuellement le backfill est manuel. Le mettre dans un workflow `padel-historical-doinsport.yml` mensuel pour rattraper les nouvelles données historiques (parfois les clubs ajoutent leurs vieux planning post-mortem).
**Effort** : trivial — copier doinsport_backfill.py en workflow.

### 4. Géocodage des UrbanPadel + des Doinsport manquants
**Problème** : 4 UrbanPadel + 6 Doinsport n'ont pas de coords GPS → invisibles sur la carte.
**Solution** : géocoder via Nominatim sur leur address.
**Effort** : trivial (~5 min).

### 5. Calcul taux d'occupation cross-plateforme
**Idée** : pour les 19 clusters multi-plateformes, l'union des réservations détectées sur les 2-3 plateformes donne une mesure plus fiable du taux d'occupation réel du club physique.
**Formule** : `occupation_unifiée = |slots_réservés_unionne_toutes_sources| / |slots_proposés_toutes_sources|`
**Effort** : moyen — nécessite logique de matching temporel des slots (un slot 14h Anybuddy = un slot 14h Playtomic ? probablement).

### 6. Détection automatique de nouveaux clubs
**Idée** : aujourd'hui on a 130 clubs ; le marché padel grandit de 30%/an. Un workflow hebdomadaire qui :
1. Re-scrape les sitemaps Anybuddy + listing Doinsport + listing Playtomic
2. Diff avec notre catalogue
3. Auto-ajout au workflow live
**Effort** : moyen (~1h).

### 7. Dashboard public-facing (B2B)
**Idée** : la page actuelle est interne. Un mini-dashboard public `padel-idf-publique.html` avec :
- Top 10 clubs par taux d'occupation (anonymisé : "Club #1 78%, Club #2 71%")
- Évolution du marché (volume + prix moyen) sur la période
- Carte chaleur Paris
**Acheteurs cibles** : investisseurs, promoteurs immobilier loisirs, fonds infra.
**Effort** : 2-3h.

### 8. Calcul du CA estimé annuel par club (Doinsport surtout)
**Idée** : avec l'historique Doinsport on a les prix réels payés sur 1-3 ans. On peut calculer le CA réel et la croissance YoY.
**Formule** : `CA_annuel = somme(price) sur 12 mois glissants` (déjà nettoyé en €, pas en cents).
**Effort** : faible (1 requête SQL).

### 9. Détection des opérateurs / chaînes
**Idée** : 4PADEL a 5+ sites en IDF, Casa Padel en a 3, Sportfield 2-3. Identifier les groupes pour faire des analyses agrégées par enseigne.
**Solution** : table `padel_brands` qui mappe slug → enseigne mère.
**Effort** : moyen — manuel pour démarrer (200 clubs FR padel, ~30 enseignes notables).

### 10. Vue carte avancée
**Idées d'enrichissements** :
- Cluster markers (LeafletMarkerCluster) pour ne pas surcharger à dézoomée
- Filtres : "uniquement clubs avec créneaux dispos dans les 7 jours", "prix < 30€/h", "indoor only"
- Choroplèthe par département (couleur = densité)
- Polygone IDF + Petite Couronne / Grande Couronne en overlay
- Mode "heatmap" (occupation par zone)
**Effort** : 2-3h cumulé.

## Améliorations long terme

### 11. Pappers integration (cf. discussion précédente)
SIREN par club → CA officiel, charges, EBE → marges réelles.
Permet l'analyse "club rentable" vs "club en perte".

### 12. Extension géographique FR
Aujourd'hui IDF only. Lyon, Marseille, Toulouse, Bordeaux sont aussi importants. Doinsport et Playtomic ont déjà 400+ clubs FR au total ; il suffit d'élargir le filtre.

### 13. Extension sports (Anybuddy multi-activité)
Aquaboulevard fait padel + tennis + squash. Notre scraper sait le faire mais ne le fait que pour padel. Étendre la couverture multi-raquette pourrait être pertinent.

### 14. Notifications utilisateurs / API publique
Si on veut monétiser : API REST sur Supabase pour des clients tiers (FFT, FFTennis, presse spé).

## Limites / risques connus

- **Légalité** : on scrape des données publiques d'APIs ouvertes. Pas de captcha, pas de login. Mais les TOS de chaque plateforme peuvent restreindre l'usage commercial. À vérifier au moment de la commercialisation.
- **Fragilité** : Doinsport pourrait fermer son endpoint public à tout moment. Avoir un plan B (Playwright).
- **Volume Supabase** : si on backfill tous les sports + toutes les régions FR, on peut taper les limites du plan gratuit. Surveiller.
