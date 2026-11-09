# openfirm-data

Collecteur des données publiques utilisées par OpenFirm :

- **FOSC** (Feuille officielle suisse du commerce), rubriques registre du commerce (HR) et faillites (KK), via l'API ouverte du Portail des feuilles officielles du SECO.
- **LINDAS** (données liées de l'administration fédérale), graphe Zefix de l'Office fédéral de la justice.

Le rythme est volontairement lent : une requête à la fois, environ 3 par seconde au plus.

Les avis de faillite de personnes physiques ne sont pas conservés. Les fichiers produits sont chiffrés avant publication dans les releases de ce dépôt. Ils ne sont pas destinés à être réutilisés tels quels.

Ceci n'est pas une publication officielle. Seules font foi les données du Portail des feuilles officielles que le SECO a pourvues d'une signature ou d'un cachet électroniques.
