#!/usr/bin/env python3
"""Historique d'avant septembre 2018 (12.11.2026) : publications FOSC d'une société, depuis son inscription.

Pourquoi : l'API FOSC actuelle ne remonte qu'au 3.9.2018. Une personne inscrite avant n'est ensuite souvent citée
que par son nom court (« Payer Laurent est nommé administrateur »), et le titulaire d'une entreprise individuelle
inscrite avant 2018 n'est jamais nommé. Les publications plus anciennes le disent (« Titulaire: Payer Laurent,
d'Echallens, à Chexbres »).

Sources publiques, sans identifiant, lues lentement :
1. Zefix (www.zefix.ch, données du registre du commerce, OGD « open use, must provide the source ») : recherche par
   IDE, puis fiche avec les publications FOSC récentes (texte compris, environ depuis 2016).
2. Archive FOSC (www.shab.ch/shabforms, 2001-2018) : chaque publication cite la précédente
   (« FOSC du 29.06.2017, p. 0/3612107 ») ; on remonte la chaîne publication par publication (PDF) jusqu'à
   l'inscription, ou jusqu'à PROFONDEUR publications.

Rythme : une requête à la fois, au plus une toutes les PAUSE secondes ; sur 403/429 répétés, arrêt propre (on garde
ce qui est fait). Sortie chiffrée comme le reste du dépôt (cle_publique.pem). Les journaux (publics) n'affichent
jamais de texte de publication ni de nom.

Usage : python3 historique_zefix.py zefix/lot-p1-01.txt   -> out/zefix-lot-p1-01.jsonl.gz.enc
Ligne de sortie : {"uid", "ehraid", "zefix": [{"d","id","t"}...], "shab": [{"id","d","t"}...], "fin": raison}
"""
import io
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import run  # noqa: E402  (chiffrement et écriture identiques au collecteur FOSC)

Z = "https://www.zefix.ch/ZefixREST/api/v1"
SHAB = "https://www.shab.ch/shabforms/servlet/Search?EID=7&DOCID=%s"
UA = "OpenFirm-collecteur/0.3 (historique public du registre du commerce; contact@openfirm.ch)"
PAUSE = float(os.environ.get("OF_PAUSE", "0.5"))
PROFONDEUR = int(os.environ.get("OF_PROFONDEUR", "30"))
LIMITE_S = int(os.environ.get("OF_LIMITE_S", str(5 * 3600)))   # s'arrête proprement avant la limite GitHub (6 h)
AVANT = "2018-09-03"
S = requests.Session()
S.headers["User-Agent"] = UA
_last = [0.0]
refus = [0]
R_CITE = re.compile(r"(?:FOSC|SHAB|FUSC|SOGC)\s+(?:du|vom|del|of)?\s*(\d{1,2})\.(\d{1,2})\.(\d{4}),?\s*(?:p\.|S\.|pag\.)\s*"
                    r"(?:\d+\s*/\s*)?(\d{5,8})")
R_TAG = re.compile(r"<[^>]+>")
R_CITE_ANCIENNE = re.compile(r"(?:FOSC|SHAB|FUSC|SOGC)[^;)]{0,40}\d{1,2}\.\d{1,2}\.(?:19|20)\d\d")


class Refus(Exception):
    pass


def req(method, url, **kw):
    for a in range(5):
        d = time.time() - _last[0]
        if d < PAUSE:
            time.sleep(PAUSE - d)
        _last[0] = time.time()
        try:
            r = S.request(method, url, timeout=60, **kw)
        except requests.RequestException as e:
            print("réseau", type(e).__name__, file=sys.stderr)
            time.sleep(5 * 2 ** a)
            continue
        if r.status_code in (200, 404):
            refus[0] = 0
            return r
        if r.status_code in (403, 429):
            refus[0] += 1
            print("refus", r.status_code, "(%d de suite)" % refus[0], file=sys.stderr)
            if refus[0] >= 5:
                raise Refus()
            time.sleep(60 * (a + 1))
            continue
        print("HTTP", r.status_code, file=sys.stderr)
        time.sleep(5 * 2 ** a)
    return None


def texte_pdf(b):
    from pypdf import PdfReader
    try:
        t = " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(b)).pages)
    except Exception:
        return None
    return re.sub(r"\s+", " ", t).strip()


def citation(t):
    """Publication précédente citée : (date ISO, numéro) de la première citation trouvée, sinon None."""
    m = R_CITE.search(t or "")
    if not m:
        return None
    j, mo, a, n = m.groups()
    return "%s-%02d-%02d" % (a, int(mo), int(j)), n


def societe(uid):
    u = uid.replace("-", "").replace(".", "")
    r = req("POST", Z + "/firm/search.json", json={"name": uid, "languageKey": "fr", "maxEntries": 5},
            headers={"Accept": "application/json"})
    if r is None or r.status_code != 200:
        return {"uid": uid, "fin": "recherche"}
    lst = [x for x in (r.json().get("list") or []) if x.get("uid") == u]
    if not lst:
        return {"uid": uid, "fin": "absente"}
    eh = lst[0]["ehraid"]
    r = req("GET", Z + "/firm/%s.json" % eh, headers={"Accept": "application/json"})
    if r is None or r.status_code != 200:
        return {"uid": uid, "ehraid": eh, "fin": "fiche"}
    d = r.json()
    pubs = sorted(d.get("shabPub") or [], key=lambda p: p.get("shabDate") or "")
    zef = [{"d": p.get("shabDate"), "id": p.get("shabId"), "t": R_TAG.sub("", p.get("message") or "")}
           for p in pubs if (p.get("shabDate") or "9") < AVANT]
    out = {"uid": uid, "ehraid": eh, "zefix": zef, "shab": [], "anciens_noms": d.get("oldNames") or []}
    # point de départ de la chaîne : la plus ancienne publication connue (Zefix), sinon la plus ancienne de Zefix
    # même après 2018 (elle cite la dernière d'avant)
    depart = pubs[0] if pubs else None
    t = R_TAG.sub("", depart.get("message") or "") if depart else ""
    vus = {str(p.get("shabId")) for p in pubs}
    for _ in range(PROFONDEUR):
        c = citation(t)
        if not c:
            out["fin"] = ("citation_ancienne" if R_CITE_ANCIENNE.search(t) else "inscription") if t else \
                "sans_publication"
            return out
        date, n = c
        if n in vus:
            out["fin"] = "boucle"
            return out
        vus.add(n)
        r = req("GET", SHAB % n)
        if r is None or r.status_code != 200 or not r.content.startswith(b"%PDF"):
            out["fin"] = "archive_absente"
            return out
        t = texte_pdf(r.content)
        if not t:
            out["fin"] = "pdf_illisible"
            return out
        out["shab"].append({"id": n, "d": date, "t": t[:20000]})
    out["fin"] = "profondeur"
    return out


def main():
    lot = sys.argv[1]
    uids = [l.strip() for l in open(lot) if l.strip()]
    nom = "zefix-" + os.path.splitext(os.path.basename(lot))[0]
    t0 = time.time()
    stats = {}

    def collecte(w):
        for i, uid in enumerate(uids):
            if time.time() - t0 > LIMITE_S:
                print("limite de temps : %d sociétés sur %d" % (i, len(uids)), file=sys.stderr)
                w({"reprise": uids[i:]})
                return
            try:
                o = societe(uid)
            except Refus:
                print("ARRÊT : refus répétés après %d sociétés" % i, file=sys.stderr)
                w({"reprise": uids[i:]})
                return
            stats[o.get("fin")] = stats.get(o.get("fin"), 0) + 1
            w(o)
            if (i + 1) % 200 == 0:
                print("%d/%d sociétés, %.0f s, %s" % (i + 1, len(uids), time.time() - t0, stats), file=sys.stderr,
                      flush=True)
        print("fini : %d sociétés, %.0f s, %s" % (len(uids), time.time() - t0, stats), file=sys.stderr)
    run(nom, collecte, "out", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cle_publique.pem"))


if __name__ == "__main__":
    main()
