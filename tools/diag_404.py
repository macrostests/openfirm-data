#!/usr/bin/env python3
"""Diagnostic en lecture seule (GET publics sur openfirm.ch) : fiches et pages personnes des sitemaps qui répondent 404.

Aucun nom n'est écrit dans le journal : seulement des comptes et des numéros IDE (registre public) ou des empreintes.
"""
import gzip
import os
import random
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://openfirm.ch"
UA = "OpenFirm-controle/1.0 (lecture seule)"
TODAY = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y-%m-%d")


def get(url, method="GET"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, method=method)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=30) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def locs(xml):
    return re.findall(r"<loc>([^<]+)</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?", xml)


def balayage():
    """Toutes les fiches des sitemaps (HEAD) : liste des IDE en 404, à refaire une nuit (forcer)."""
    st, idx = get(BASE + "/sitemap.xml")
    maps = [u for u, _ in locs(idx.decode())]
    fiches = []
    for sm in maps:
        st, b = get(sm)
        for u, lm in locs(b.decode()) if st == 200 else []:
            if re.search(r"/(entreprise|firma|impresa|company)/che\d{9}-", u):
                fiches.append(u)
    print("fiches à tester : %d" % len(fiches), flush=True)
    t0 = time.time()
    res = []
    with ThreadPoolExecutor(48) as ex:
        for i in range(0, len(fiches), 20000):
            part = list(ex.map(lambda u: (u, get(u, "HEAD")[0]), fiches[i:i + 20000]))
            res += part
            ko = [re.search(r"che(\d{9})", u).group(1) for u, s in part if s == 404]
            print("lot %d : %d testées, 404 : %s" % (i // 20000, len(part), " ".join(ko)), flush=True)
    cpt = {}
    for _, s in res:
        cpt[s] = cpt.get(s, 0) + 1
    print("résultat : %s (%.0f s)" % (dict(sorted(cpt.items())), time.time() - t0))
    ko = sorted({re.search(r"che(\d{9})", u).group(1) for u, s in res if s == 404})
    autres = sorted({re.search(r"che(\d{9})", u).group(1) for u, s in res if s not in (200, 404)})
    print("IDE_404 " + " ".join(ko))
    print("IDE_AUTRES " + " ".join(autres))


def main():
    st, idx = get(BASE + "/sitemap.xml")
    maps = [u for u, _ in locs(idx.decode())]
    print("sitemaps : %d" % len(maps))
    fiches, personnes = [], []
    for sm in maps:
        st, b = get(sm)
        if st != 200:
            print("sitemap %s : %d" % (sm.rsplit("/", 1)[-1], st))
            continue
        for u, lm in locs(b.decode()):
            if re.search(r"/(entreprise|firma|impresa|company)/che\d{9}-", u):
                fiches.append((u, lm))
            elif re.search(r"/(personne|person|persona)/", u) and re.search(r"-[0-9a-f]{8}/$", u):
                personnes.append((u, lm))
        time.sleep(0.2)
    print("fiches dans les sitemaps : %d ; pages personnes : %d" % (len(fiches), len(personnes)))
    random.seed(20261124)
    groupes = {
        "fiches lastmod aujourd'hui": [u for u, lm in fiches if lm == TODAY],
        "fiches au hasard": [u for u, _ in random.sample(fiches, min(1500, len(fiches)))],
        "personnes lastmod aujourd'hui": [u for u, lm in personnes if lm == TODAY],
        "personnes au hasard": [u for u, _ in random.sample(personnes, min(1000, len(personnes)))],
    }
    for nom, urls in groupes.items():
        total = len(urls)
        urls = urls if len(urls) <= 3000 else random.sample(urls, 3000)
        with ThreadPoolExecutor(6) as ex:
            res = list(ex.map(lambda u: (u, get(u, "HEAD")[0]), urls))
        cpt = {}
        for _, s in res:
            cpt[s] = cpt.get(s, 0) + 1
        ex404 = [re.search(r"che(\d{9})", u).group(1) if "che" in u else u[-9:-1] for u, s in res if s == 404][:15]
        print("%s : %d dans les sitemaps, %d testées -> %s ; exemples 404 : %s" % (
            nom, total, len(urls), dict(sorted(cpt.items())), ", ".join(ex404)))
    # page 404 du site : une adresse inexistante doit renvoyer la page du site, pas « Not found »
    for u in ("/fr/entreprise/che000000001-x/", "/fr/nexiste-pas/"):
        st, b = get(BASE + u)
        print("404 %s : %d, %s" % (u, st, "page du site" if b"<html" in b[:500].lower() else repr(b[:40])))


if __name__ == "__main__":
    if os.environ.get("BALAYAGE") == "1":
        balayage()
    else:
        main()
