# 8 insights padel IDF qu'on peut creuser maintenant (sans nouvelle scrap)

Tous calculables sur ce qu'on a déjà en base : 146 856 bookings historiques Doinsport (54 clubs) + 38 846 sessions live (143 clubs) + GPS + INSEE + météo. Ordre suggéré par impact / facilité d'exécution.

---

## 1. 🕐 Cartographie horaire de la demande (heatmap)

**Question** : à quelle heure tu veux ouvrir un nouveau club ? Quelles plages sont saturées vs creuses ?

**Méthode** : croiser tous les bookings (live + history) par jour de semaine × heure (lundi 7h, lundi 8h, …, dimanche 22h) sur 7 × 18 = 126 cases. Heat-mappable.

**Output attendu** : visualisation 2D + ranking des 20 créneaux où la demande est la plus forte (probable : jeudi 19h, vendredi 19h-20h, samedi 10h, samedi 17h-20h, dimanche 10h-12h).

**Valeur business** : un investisseur saura qu'avant d'ouvrir un nouveau club, il faut viser ces créneaux saturés → modèle de demande validé.

---

## 2. 📊 Pareto : combien de clubs font 80% du marché ?

**Question** : le marché est-il concentré (quelques gros opérateurs) ou éclaté (longue traîne) ?

**Méthode** : trier les clubs par volume de bookings, calculer cumulé, identifier où on atteint 50% / 80% / 95% du total.

**Output attendu** : *"Sur Doinsport IDF, les top 10 clubs (18% du panel) font 70% des bookings"* — signature de concentration claire.

**Valeur business** : oriente la stratégie d'acquisition (cibler quelques leaders ou multiplier les petites prises ?).

---

## 3. 💰 Élasticité prix vs occupation

**Question** : un club premium (80€/h) est-il moins rempli qu'un club abordable (45€/h) ? Ou les premium ont une demande inélastique ?

**Méthode** : scatter plot prix médian 90min × taux d'occupation (où on a la mesure, donc bookings Doinsport / créneaux estimés). Régression linéaire.

**Output attendu** : *"L'élasticité est faible (-0.3 estimée) : doubler le prix réduit la demande de 30%, pas de 100%. Le padel est un bien premium peu sensible au prix dans cette gamme."* — ou l'inverse.

**Valeur business** : pricing power réel des opérateurs.

---

## 4. 🌍 Concentration géographique : zones saturées vs vides

**Question** : où ouvrir le prochain club ? Quelle commune a 30 000 habitants et 0 club à moins de 5 km ?

**Méthode** : carte choroplèthe par maille communale ou EPCI, croiser nb_clubs_dans_5km × population. Identifier les "déserts padel" (population dense + 0 club proche).

**Output attendu** : *"Top 5 zones déficitaires IDF : Évry-Sud, Argenteuil-Est, Saint-Maur-des-Fossés, Bobigny-Centre, Massy."* (à confirmer).

**Valeur business** : leads d'implantation directes pour investisseurs / promoteurs loisirs.

---

## 5. 🚀 Détecter les "rocket clubs" en hyper-croissance

**Question** : quels clubs ouvrent en 2025-2026 et explosent ? Quels clubs anciens stagnent ?

**Méthode** : pour chaque club Doinsport, calculer le delta volume (mois courant vs même mois N-1) ou pente régression linéaire sur 6 mois glissants.

**Output attendu** : ranking *"clubs qui croissent le plus vite"* (probablement B14 qui ouvre 2025-08 et a déjà 13k bookings, ou les clubs avec premier_booking récent) vs *"clubs qui stagnent / déclinent"*.

**Valeur business** : repérer les opérateurs en train de prendre des parts de marché, signaler les modèles qui marchent.

---

## 6. 🏢 Chaînes vs indépendants : qui performe le mieux ?

**Question** : 4PADEL / Casa Padel / UrbanPadel etc. sont-ils plus rentables que les clubs indépendants ?

**Méthode** : pour les 15 enseignes détectées (4PADEL, Casa, Sportfield, etc.) vs reste, comparer :
- volume médian/club
- prix médian
- croissance YoY (où on a l'historique)

**Output attendu** : *"Les clubs sous enseigne ont +35% de volume médian vs indépendants, mais facturent en moyenne 8€/h plus cher."* (ou pas, à voir).

**Valeur business** : pour un investisseur potentiel, savoir si racheter une franchise vs racheter un indépendant est meilleur ROI.

---

## 7. 📅 Effet vacances scolaires + jours fériés

**Question** : le marché chute pendant les vacances ? Ou explose ?

**Méthode** : surimposer le calendrier vacances scolaires zone C (IDF) + jours fériés sur la courbe mensuelle. Calculer delta volume jours "normaux" vs jours "vacances" (probablement zonal : 75 part en vacances vs 77 part pas).

**Output attendu** : *"Vacances Pâques : -15% volume Paris intra (75) mais +8% grande couronne (77/91/95) — l'IDF se déplace en banlieue."* (hypothèse à valider).

**Valeur business** : optimisation pricing dynamique (peak/off-peak), staffing.

---

## 8. 🔗 Avantage du multi-plateforme : les 19 clusters

**Question** : les clubs distribuant sur 2-3 plateformes ont-ils vraiment plus de bookings que les mono-plateforme ?

**Méthode** : comparer volume médian par cluster (les 19 unifiés) vs volume médian des clubs mono-plateforme. Contrôler par taille (nb playgrounds).

**Output attendu** : *"Les clubs multi-plateformes ont +X% bookings agrégés vs mono. Le gain net (après commission plateforme estimée à 5-10%) est de +Y%."*

**Valeur business** : recommandation aux clubs : multi-distribuer ou pas ?

---

## 🎁 Bonus : insights "tactiques" rapides à shipper

- **9. Prix sur l'évolution 2024 → 2026** : un même club a-t-il monté ses prix ? De combien ?
- **10. Effet "Roland Garros"** : pic de popularité en juin (pour le tournoi tennis) ?
- **11. Aperçu des coachs / influenceurs** : noms qui reviennent dans les bookings avec "lesson"
- **12. Sous-population : tournois vs créneaux libres** : quel % du volume est compétition ?
- **13. Taux de waitlist** : Anybuddy expose-t-il les listes d'attente ?

---

**Mes recommandations priorité** :
1. **#1 Heatmap horaire** (immédiat, très visuel, valeur claire)
2. **#5 Rocket clubs** (cible directe les investisseurs)
3. **#4 Déserts padel** (génère des leads d'implantation)

Tu veux que je ship les 3 d'un coup, ou tu veux choisir un ordre ?
