#!/usr/bin/env python3
"""Sites web des sociétés suisses depuis Common Crawl (archive ouverte du web), essai du 19.11.2026.

Idée : beaucoup de sites suisses affichent leur numéro IDE (CHE-xxx.xxx.xxx) dans les mentions légales ou le pied de
page. Une page .ch qui affiche un IDE valide (chiffre de contrôle juste) relie ce site à la société avec une très
bonne sûreté. Rien n'est demandé aux sites eux-mêmes : on lit seulement les copies déjà archivées par Common Crawl.

Étape 1 (index) : index Parquet de Common Crawl, lu par DuckDB en ne prenant que les morceaux utiles (.ch).
Étape 2 (lire) : lecture d'un échantillon de pages archivées (requêtes « Range » sur data.commoncrawl.org).
Sortie : statistiques dans le journal (aucun nom ni domaine) + fichier chiffré domaine → IDE (release « sites »).
"""
import argparse
import concurrent.futures as cf
import gzip
import io
import json
import os
import random
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect import run  # noqa: E402  (chiffrement identique aux autres collectes)

BASE = "https://data.commoncrawl.org/"
UA = "OpenFirm-sites/0.1 (+https://openfirm.ch ; contact@openfirm.ch)"
PAGES_LEGALES = r"(impressum|imprint|mentions|legal|rechtlich|kontakt|contact|about|ueber-uns|uber-uns|qui-sommes|chi-siamo|note-legali|colophon|agb)"
RE_IDE = re.compile(r"CHE[\s\-‐-–.]{0,2}(\d{3})[\s.  ']?(\d{3})[\s.  ']?(\d{3})(?!\d)")
POIDS = (5, 4, 3, 2, 7, 6, 5, 4)


def ide_valide(d):
    s = sum(int(c) * p for c, p in zip(d[:8], POIDS))
    k = 11 - s % 11
    k = 0 if k == 11 else k
    return k != 10 and k == int(d[8])


def get(url, rng=None, essais=4):
    for i in range(essais):
        try:
            h = {"User-Agent": UA}
            if rng:
                h["Range"] = "bytes=%d-%d" % rng
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                return r.read()
        except Exception as e:  # 503 = trop de requêtes chez Common Crawl : on attend
            if i == essais - 1:
                raise
            time.sleep(2 ** i * 3)


def derniere_collecte():
    j = json.loads(get("https://index.commoncrawl.org/collinfo.json"))
    return j[0]["id"]


def fichiers_index(crawl):
    lst = gzip.decompress(get(BASE + "crawl-data/%s/cc-index-table.paths.gz" % crawl)).decode().split()
    return [BASE + p for p in lst if "/subset=warc/" in p]


def interroger(fichiers, limite_par_fichier, graine):
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET http_retries=5; SET http_keep_alive=false;")
    stats = {"pages_ch": 0, "pages_legales": 0, "pages_accueil": 0}
    domaines, domaines_leg = set(), set()
    cand = []

    def un(f, essai=0):
        try:
            return un1(f)
        except Exception as e:  # Common Crawl coupe parfois : on attend et on recommence
            if essai >= 5:
                print("index : abandon d'un fichier après 6 essais (%s)" % str(e)[:80], file=sys.stderr)
                return (0, 0), []
            time.sleep(10 * (essai + 1))
            return un(f, essai + 1)

    def un1(f):
        c = duckdb.connect()
        c.execute("LOAD httpfs; SET http_retries=5;")
        tot = c.execute("SELECT count(*), count(DISTINCT url_host_registered_domain) FROM read_parquet(?) "
                        "WHERE url_host_tld = 'ch'", [f]).fetchone()
        rows = c.execute(
            "SELECT url, url_host_registered_domain, warc_filename, warc_record_offset, warc_record_length, "
            "regexp_matches(lower(url_path), ?) AS legale "
            "FROM read_parquet(?) WHERE url_host_tld = 'ch' AND fetch_status = 200 "
            "AND content_mime_detected = 'text/html' "
            "AND (regexp_matches(lower(url_path), ?) OR url_path IN ('/', '', '/de/', '/fr/', '/it/', '/en/'))",
            [PAGES_LEGALES, f, PAGES_LEGALES]).fetchall()
        return tot, rows

    with cf.ThreadPoolExecutor(4) as ex:
        for k, (tot, rows) in enumerate(ex.map(un, fichiers)):
            stats["pages_ch"] += tot[0]
            for r in rows:
                domaines.add(r[1])
                if r[5]:
                    stats["pages_legales"] += 1
                    domaines_leg.add(r[1])
                else:
                    stats["pages_accueil"] += 1
            cand.extend(rows)
            print("index %d/%d : %d pages .ch, %d candidates" % (k + 1, len(fichiers), tot[0], len(rows)),
                  file=sys.stderr, flush=True)
    stats["domaines_avec_candidate"] = len(domaines)
    stats["domaines_avec_page_legale"] = len(domaines_leg)
    return stats, cand


def lire(rec):
    url, dom, wf, off, ln, legale = rec
    raw = get(BASE + wf, (off, off + ln - 1))
    data = gzip.decompress(raw)
    # WARC → en-têtes WARC, en-têtes HTTP, corps
    parts = data.split(b"\r\n\r\n", 2)
    corps = parts[2] if len(parts) == 3 else b""
    entetes = parts[1].lower() if len(parts) > 1 else b""
    if corps[:2] == b"\x1f\x8b":
        try:
            corps = gzip.decompress(corps)
        except Exception:
            pass
    elif b"transfer-encoding: chunked" in entetes:
        corps = re.sub(rb"\r\n[0-9a-fA-F]{1,6}\r\n", b"", corps)
    txt = corps[:2_000_000].decode("utf-8", "replace")
    txt = re.sub(r"<[^>]+>", " ", txt)
    ides = sorted({"CHE-%s.%s.%s" % m for m in RE_IDE.findall(txt) if ide_valide("".join(m))})
    sorte = ("impressum" if re.search(r"(impressum|imprint|mentions|legal|rechtlich|note-legali|colophon)", url.lower())
             else "contact" if legale else "accueil")
    return {"domaine": dom, "url": url, "legale": bool(legale), "sorte": sorte, "ides": ides}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawl")
    ap.add_argument("--fichiers", type=int, default=0, help="nombre de fichiers d'index (0 = tous)")
    ap.add_argument("--echantillon", type=int, default=20000)
    ap.add_argument("--fils", type=int, default=16)
    ap.add_argument("--out", default="out")
    ap.add_argument("--pub", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cle_publique.pem"))
    a = ap.parse_args()
    crawl = a.crawl or derniere_collecte()
    fichiers = fichiers_index(crawl)
    if a.fichiers:
        fichiers = random.Random(1).sample(fichiers, a.fichiers)
    t0 = time.time()
    stats, cand = interroger(fichiers, max(1, a.echantillon // len(fichiers) + 1), 20261119)
    print("collecte %s, %d fichiers d'index lus en %d s" % (crawl, len(fichiers), time.time() - t0), file=sys.stderr)
    random.Random(7).shuffle(cand)
    cand = cand[:a.echantillon]
    res, erreurs = [], 0
    t1 = time.time()
    with cf.ThreadPoolExecutor(a.fils) as ex:
        futs = [ex.submit(lire, r) for r in cand]
        for i, f in enumerate(cf.as_completed(futs)):
            try:
                res.append(f.result())
            except Exception:
                erreurs += 1
            if (i + 1) % 2000 == 0:
                print("lu %d/%d (%d erreurs)" % (i + 1, len(cand), erreurs), file=sys.stderr, flush=True)
    dur = time.time() - t1
    avec = [r for r in res if r["ides"]]
    dom_lus = {r["domaine"] for r in res}
    dom_ide = {r["domaine"] for r in avec}
    ides = {i for r in avec for i in r["ides"]}
    leg = [r for r in res if r["legale"]]
    acc = [r for r in res if not r["legale"]]
    stats.update({
        "crawl": crawl, "fichiers_index": len(fichiers), "pages_lues": len(res), "erreurs": erreurs,
        "secondes_lecture": int(dur), "pages_par_seconde": round(len(res) / max(dur, 1), 1),
        "pages_avec_ide": len(avec),
        "taux_pages_legales": round(sum(1 for r in leg if r["ides"]) / max(len(leg), 1), 3),
        "taux_pages_accueil": round(sum(1 for r in acc if r["ides"]) / max(len(acc), 1), 3),
        "par_sorte": {so: [sum(1 for r in res if r["sorte"] == so),
                           sum(1 for r in res if r["sorte"] == so and r["ides"])] for so in ("impressum", "contact", "accueil")},
        "domaines_lus": len(dom_lus), "domaines_avec_ide": len(dom_ide),
        "taux_domaines": round(len(dom_ide) / max(len(dom_lus), 1), 3),
        "ide_distincts": len(ides),
        "pages_avec_plusieurs_ide": sum(1 for r in avec if len(r["ides"]) > 1),
    })
    print("STATS " + json.dumps(stats, ensure_ascii=False), file=sys.stderr)
    nom = "sites-essai-%s" % crawl
    run(nom, lambda w: [w(r) for r in avec] and None, a.out, a.pub)
    json.dump(stats, open(os.path.join(a.out, nom + ".stats.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
