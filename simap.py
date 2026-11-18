#!/usr/bin/env python3
"""Collecteur simap.ch (marchés publics suisses) pour OpenFirm.

Source : API publique de simap.ch, sans compte (CG « Utilisation de l'API de simap.ch »,
version du 30.9.2023 : usage commercial et transmission à des tiers autorisés ; contenu non
modifié ; mention « Ceci n'est pas une publication officielle. Les données publiées sur la
plateforme www.simap.ch font foi. » ; diffusion dès 8 h le jour de publication).

Pour chaque projet dont la publication la plus récente tombe dans la période :
  - la ligne de la liste (titre, acheteur, type, dates, lots…) ;
  - le détail de cette publication (adjudication : gagnants, prix, nombre d'offres ; appel
    d'offres : délais, CPV, description…).
Sortie : JSONL compressé et chiffré (même format que collect.py), une ligne par projet :
  {"liste": {...}, "detail": {...}}

Rythme : PAUSE secondes entre deux requêtes de la même tâche (une connexion à la fois),
pause croissante en cas de réponse 429/5xx. Plusieurs tâches tournent en parallèle dans
GitHub Actions (une par trimestre).
"""
import argparse
import datetime as dt
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import run  # noqa: E402  (chiffrement + gzip identiques à la FOSC)

BASE = "https://www.simap.ch/rest/publications"
UA = "OpenFirm-collecteur/0.3 (API publique simap.ch; contact via github.com/macrostests/openfirm-data)"
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/json"})
PAUSE = [0.6]
_last = [0.0]
STATS = {"requetes": 0, "erreurs": 0, "projets": 0, "details": 0}


def get(path, params=None, tries=7):
    for a in range(tries):
        d = time.time() - _last[0]
        if d < PAUSE[0]:
            time.sleep(PAUSE[0] - d)
        _last[0] = time.time()
        STATS["requetes"] += 1
        try:
            r = S.get(BASE + path, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            STATS["erreurs"] += 1
            print("HTTP", r.status_code, path, r.text[:200], file=sys.stderr)
            if r.status_code in (429, 500, 502, 503, 504):
                PAUSE[0] = min(5.0, PAUSE[0] * 2)  # on ralentit pour toute la suite
        except requests.RequestException as e:
            STATS["erreurs"] += 1
            print("réseau", e, file=sys.stderr)
        time.sleep(min(120, 5 * 2 ** a))
    raise RuntimeError("échec %s %s" % (path, params))


def semaines(debut, fin):
    d = debut
    while d <= fin:
        f = min(fin, d + dt.timedelta(days=6))
        yield d, f
        d = f + dt.timedelta(days=1)


def lister(d1, d2):
    """Tous les projets dont la publication la plus récente est entre d1 et d2 (inclus)."""
    last, vus = None, set()
    while True:
        p = {"lang": "fr", "orderAddressCountryOnlySwitzerland": "false",
             "newestPublicationFrom": d1.isoformat(), "newestPublicationUntil": d2.isoformat()}
        if last:
            p["lastItem"] = last
        j = get("/v2/project/project-search", p) or {}
        projets = j.get("projects") or []
        for x in projets:
            if x["publicationId"] not in vus:
                vus.add(x["publicationId"])
                yield x
        last = (j.get("pagination") or {}).get("lastItem")
        if not last or len(projets) < 20:
            return


def collecter(debut, fin, writer):
    for d1, d2 in semaines(debut, fin):
        n = 0
        for x in lister(d1, d2):
            det = get("/v1/project/%s/publication-details/%s" % (x["id"], x["publicationId"]))
            writer({"liste": x, "detail": det})
            n += 1
            STATS["projets"] += 1
            STATS["details"] += det is not None
        print("semaine", d1, d2, n, "projets", STATS, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["periode", "jour"])
    ap.add_argument("arg", nargs="?", help="periode : AAAA-MM-JJ:AAAA-MM-JJ ; jour : AAAA-MM-JJ (défaut hier)")
    ap.add_argument("--pause", type=float, default=0.6)
    ap.add_argument("--out", default="out")
    ap.add_argument("--pub", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cle_publique.pem"))
    a = ap.parse_args()
    PAUSE[0] = a.pause
    hier = dt.date.today() - dt.timedelta(days=1)
    if a.mode == "jour":
        j = dt.date.fromisoformat(a.arg) if a.arg else hier
        d1 = d2 = j
        nom = "simap-jour-%s" % j.isoformat()
    else:
        s1, s2 = a.arg.split(":")
        d1, d2 = dt.date.fromisoformat(s1), min(dt.date.fromisoformat(s2), hier)
        nom = "simap-%s_%s" % (d1.isoformat(), d2.isoformat())
    t0 = time.time()
    run(nom, lambda w: collecter(d1, d2, w), a.out, a.pub)
    print("fini", nom, STATS, "%.0f s" % (time.time() - t0), file=sys.stderr)


if __name__ == "__main__":
    main()
