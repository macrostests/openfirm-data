#!/usr/bin/env python3
"""Mesure à quelle heure la FOSC a fini de publier sa journée : nombre de publications HR et KK du jour,
relevé à plusieurs heures (une requête par rubrique, sans rien télécharger d'autre). Résultat dans le journal."""
import datetime as dt
import requests

API = "https://amtsblattportal.ch/api/v1/publications"
jour = dt.date.today().isoformat()
for r in ("HR", "KK"):
    p = [("rubrics", r), ("publicationStates", "PUBLISHED"), ("publicationDate.start", jour),
         ("publicationDate.end", jour), ("pageRequest.size", "1"), ("pageRequest.page", "0")]
    j = requests.get(API, params=p, timeout=60, headers={"Accept": "application/json"}).json()
    print("MESURE", dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), jour, r, j.get("total"))
