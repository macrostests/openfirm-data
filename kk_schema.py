"""Diagnostic sans nom (20.11) : sur des avis de faillite dont le débiteur est une personne, quelle structure ?
Affiche seulement des chemins d'éléments XML et des comptes (jamais de texte ni de nom), lisible dans un journal public.
Usage : python3 kk_schema.py AAAA-MM-JJ [AAAA-MM-JJ ...]"""
import re
import sys
from collections import Counter

import collect as C

MOTS_RI = re.compile(r"(einzelunternehmen|einzelfirma|inhaber|raison individuelle|entreprise individuelle|titulaire|"
                     r"ditta individuale|titolare|impresa individuale|sole proprietor)", re.I)
UID = re.compile(r"CHE-?\d{3}\.?\d{3}\.?\d{3}")


def chemins(o, base=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from chemins(v, base + "/" + k)
    elif isinstance(o, list):
        for v in o:
            yield from chemins(v, base + "[]")
    else:
        yield base


def texte(o):
    if isinstance(o, dict):
        return " ".join(texte(v) for v in o.values())
    if isinstance(o, list):
        return " ".join(texte(v) for v in o)
    return str(o or "")


ch = Counter()
st = Counter()
for day in sys.argv[1:]:
    for m in C.list_day(day):
        if m["r"] != "KK":
            continue
        o = C.fetch_xml(m["id"])
        if not C.person_debtor(o):
            continue
        st["avis_personne"] += 1
        st["sorte_" + (m.get("s") or "?")] += 1
        for p in set(chemins(o.get("debtor"), "debtor")):
            ch[p] += 1
        t = texte(o)
        st["avec_IDE_dans_le_texte"] += bool(UID.search(t))
        st["avec_mot_raison_individuelle"] += bool(MOTS_RI.search(t))
for k, v in sorted(st.items()):
    print("%-40s %d" % (k, v))
print("--- chemins sous debtor (nombre d'avis) ---")
for k, v in ch.most_common(60):
    print("%6d  %s" % (v, k))
