#!/usr/bin/env python3
"""Collecteur OpenFirm : FOSC (registre du commerce + faillites) et LINDAS (Zefix), toute la Suisse.

Sources officielles, ouvertes et gratuites :
- FOSC : https://amtsblattportal.ch/api/v1/publications (rubriques HR et KK)
- LINDAS : https://ld.admin.ch/query, graphe https://lindas.admin.ch/foj/zefix

Rythme poli : une seule requête à la fois, au plus ~3 requêtes par seconde, pauses sur erreur.
Minimisation : les avis de faillite de personnes physiques ne sont pas conservés (seul l'identifiant reste,
avec un drapeau), ni leur titre ni leur texte.

Les fichiers produits sont chiffrés (RSA-OAEP 4096 + AES-256-GCM) avec cle_publique.pem :
ils sont publiés dans ce dépôt public, mais seul le détenteur de la clé privée peut les lire.

Usage :
  python3 collect.py fosc-mois 2026-10           -> out/fosc-2026-10.jsonl.gz.enc
  python3 collect.py fosc-jour 2026-11-08        -> out/fosc-jour-2026-11-08.jsonl.gz.enc
  python3 collect.py lindas                      -> out/lindas-AAAA-MM-JJ.jsonl.gz.enc
  python3 collect.py dechiffrer fichier.enc --cle cle_privee.pem   (côté site)
"""
import argparse
import calendar
import datetime as dt
import gzip
import io
import json
import os
import struct
import sys
import time

import requests
from lxml import etree

API = "https://amtsblattportal.ch/api/v1/publications"
SPARQL = "https://ld.admin.ch/query"
GRAPH = "https://lindas.admin.ch/foj/zefix"
UA = "OpenFirm-collecteur/0.2 (donnees publiques FOSC et LINDAS; contact via github.com/macrostests/openfirm-data)"
DROP = {"uidOrganisationId", "uidOrganisationIdCategorie", "code13", "testImport", "translations", "senderOffice"}
PAUSE = 0.34
S = requests.Session()
S.headers["User-Agent"] = UA
_last = [0.0]


def _wait():
    d = time.time() - _last[0]
    if d < PAUSE:
        time.sleep(PAUSE - d)
    _last[0] = time.time()


def get(url, params=None, accept="application/json", tries=6):
    for a in range(tries):
        _wait()
        try:
            r = S.get(url, params=params, headers={"Accept": accept}, timeout=90)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            print("HTTP", r.status_code, url, file=sys.stderr)
        except requests.RequestException as e:
            print("réseau", e, file=sys.stderr)
        time.sleep(min(60, 3 * 2 ** a))
    raise RuntimeError("échec %s" % url)


# ---------- FOSC ----------

def compact_meta(m):
    t = m.get("title") or {}
    return {"id": m["id"], "d": (m.get("publicationDate") or "")[:10], "r": m.get("rubric"), "s": m.get("subRubric"),
            "l": m.get("language"), "c": m.get("cantons"), "n": m.get("publicationNumber"),
            "t": t.get(m.get("language")) or t.get("fr") or t.get("de") or t.get("it") or "",
            "o": (m.get("registrationOffice") or {}).get("displayName")}


def list_day(day):
    out, page = [], 0
    while True:
        p = [("rubrics", "HR"), ("rubrics", "KK"), ("publicationStates", "PUBLISHED"),
             ("publicationDate.start", day), ("publicationDate.end", day),
             ("pageRequest.size", "200"), ("pageRequest.page", str(page))]
        j = get(API, p).json()
        out += [compact_meta(c["meta"]) for c in j.get("content", [])]
        page += 1
        if page * 200 >= j.get("total", 0) or not j.get("content"):
            return out


def to_obj(el):
    kids = [c for c in el if isinstance(c.tag, str)]
    if not kids:
        return (el.text or "").strip()
    o = {}
    for c in kids:
        k = etree.QName(c).localname
        if k in DROP:
            continue
        v = to_obj(c)
        if k in o:
            if not isinstance(o[k], list):
                o[k] = [o[k]]
            o[k].append(v)
        else:
            o[k] = v
    return o


def fetch_xml(pid):
    r = get("%s/%s/xml" % (API, pid), accept="application/xml")
    if r is None:
        return None
    root = etree.fromstring(r.content)
    content = next((e for e in root.iter() if isinstance(e.tag, str) and etree.QName(e).localname == "content"), None)
    return to_obj(content) if content is not None else None


def person_debtor(o):
    d = o.get("debtor") if isinstance(o, dict) else None
    return isinstance(d, dict) and bool(d.get("person") or d.get("persons")) and not d.get("companies")


def collect_days(days, writer):
    n = np = 0
    for day in days:
        metas = list_day(day)
        print(day, len(metas), "publications", file=sys.stderr)
        for m in metas:
            o = fetch_xml(m["id"])
            if m["r"] == "KK" and person_debtor(o):
                writer({"m": {"id": m["id"], "d": m["d"], "r": "KK", "s": m["s"]}, "personne": 1})
                np += 1
                continue
            writer({"m": m, "x": o})
            n += 1
    print("total", n, "conservées,", np, "faillites de personnes réduites à l'identifiant", file=sys.stderr)


# ---------- LINDAS ----------

def sparql(q):
    for a in range(6):
        _wait()
        try:
            r = S.post(SPARQL, data={"query": q}, headers={"Accept": "application/sparql-results+json"}, timeout=300)
            if r.status_code == 200:
                return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]
            print("SPARQL HTTP", r.status_code, file=sys.stderr)
        except requests.RequestException as e:
            print("SPARQL réseau", e, file=sys.stderr)
        time.sleep(min(60, 3 * 2 ** a))
    raise RuntimeError("échec SPARQL")


P = "PREFIX schema: <http://schema.org/> "


def collect_lindas(writer):
    ms = sparql(P + "SELECT DISTINCT ?m WHERE { GRAPH <%s> { ?s a <https://schema.ld.admin.ch/ZefixOrganisation> ; "
                "<https://schema.ld.admin.ch/municipality> ?m } }" % GRAPH)
    ms = sorted(r["m"] for r in ms)
    print(len(ms), "communes", file=sys.stderr)
    total = 0
    for i in range(0, len(ms), 15):
        vals = " ".join("<%s>" % m for m in ms[i:i + 15])
        rows = sparql(P + "SELECT ?s ?m ?name ?uid ?form ?desc ?street ?zip ?loc WHERE { GRAPH <%s> { VALUES ?m { %s } "
                      "?s a <https://schema.ld.admin.ch/ZefixOrganisation> ; <https://schema.ld.admin.ch/municipality> ?m ; "
                      "schema:name ?name . "
                      "OPTIONAL { ?s schema:identifier ?id . ?id schema:value ?uid . FILTER(CONTAINS(STR(?id),\"/UID/\")) } "
                      "OPTIONAL { ?s schema:additionalType ?form } OPTIONAL { ?s schema:description ?desc } "
                      "OPTIONAL { ?s schema:address ?a . OPTIONAL { ?a schema:streetAddress ?street } "
                      "OPTIONAL { ?a schema:postalCode ?zip } OPTIONAL { ?a schema:addressLocality ?loc } } } }"
                      % (GRAPH, vals))
        orgs = {}
        for r in rows:
            o = orgs.setdefault(r["s"], {"s": r["s"], "bfs": r["m"].rsplit("/", 1)[-1], "names": [], "desc": []})
            for k_src, k in (("uid", "uid"), ("form", "form"), ("street", "street"), ("zip", "zip"), ("loc", "loc")):
                if r.get(k_src) and k not in o:
                    o[k] = r[k_src].rsplit("/", 1)[-1] if k == "form" else r[k_src]
            if r["name"] not in o["names"]:
                o["names"].append(r["name"])
            if r.get("desc") and r["desc"] not in o["desc"]:
                o["desc"].append(r["desc"])
        for o in orgs.values():
            writer(o)
        total += len(orgs)
        print(i + 15, "/", len(ms), "communes,", total, "entités", file=sys.stderr)


def collect_communes(writer):
    rows = sparql(P + "SELECT ?m ?name ?canton WHERE { GRAPH <https://lindas.admin.ch/fso/register> { "
                  "?m a <https://schema.ld.admin.ch/PoliticalMunicipality> ; schema:name ?name ; "
                  "schema:containedInPlace ?canton . FILTER(CONTAINS(STR(?canton), \"/canton/\")) "
                  "FILTER NOT EXISTS { ?m a <https://version.link/Deprecated> } } }")
    seen = set()
    for r in rows:
        bfs = r["m"].rsplit("/", 1)[-1]
        key = (bfs, r["name"])
        if key in seen:
            continue
        seen.add(key)
        writer({"bfs": bfs, "name": r["name"], "canton": r["canton"].rsplit("/", 1)[-1]})
    print(len(seen), "communes", file=sys.stderr)


# ---------- chiffrement ----------

def encrypt(data, pub_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    pub = serialization.load_pem_public_key(open(pub_path, "rb").read())
    key, nonce = AESGCM.generate_key(256), os.urandom(12)
    ek = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return b"OFE1" + struct.pack(">H", len(ek)) + ek + nonce + AESGCM(key).encrypt(nonce, data, None)


def decrypt(blob, priv_path):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    assert blob[:4] == b"OFE1"
    n = struct.unpack(">H", blob[4:6])[0]
    ek, nonce, ct = blob[6:6 + n], blob[6 + n:18 + n], blob[18 + n:]
    priv = serialization.load_pem_private_key(open(priv_path, "rb").read(), password=None)
    key = priv.decrypt(ek, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return AESGCM(key).decrypt(nonce, ct, None)


def run(name, fn, out, pub):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        def writer(o):
            gz.write(json.dumps(o, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        fn(writer)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name + ".jsonl.gz.enc")
    open(path, "wb").write(encrypt(buf.getvalue(), pub))
    print("écrit", path, os.path.getsize(path), "octets", file=sys.stderr)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["fosc-mois", "fosc-jour", "lindas", "communes", "dechiffrer"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--out", default="out")
    ap.add_argument("--pub", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cle_publique.pem"))
    ap.add_argument("--cle")
    a = ap.parse_args()
    if a.mode == "fosc-mois":
        y, m = map(int, a.arg.split("-"))
        last = calendar.monthrange(y, m)[1]
        days = ["%04d-%02d-%02d" % (y, m, d) for d in range(1, last + 1)
                if dt.date(y, m, d) < dt.date.today()]
        run("fosc-%s" % a.arg, lambda w: collect_days(days, w), a.out, a.pub)
    elif a.mode == "fosc-jour":
        day = a.arg or (dt.date.today() - dt.timedelta(days=1)).isoformat()
        run("fosc-jour-%s" % day, lambda w: collect_days([day], w), a.out, a.pub)
    elif a.mode == "lindas":
        run("lindas-%s" % dt.date.today().isoformat(), collect_lindas, a.out, a.pub)
        run("communes-%s" % dt.date.today().isoformat(), collect_communes, a.out, a.pub)
    elif a.mode == "communes":
        run("communes-%s" % dt.date.today().isoformat(), collect_communes, a.out, a.pub)
    else:
        sys.stdout.buffer.write(gzip.decompress(decrypt(open(a.arg, "rb").read(), a.cle)))


if __name__ == "__main__":
    main()
