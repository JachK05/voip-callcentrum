"""Vytvoří frontu, zavolá přes Asterisk a vyhodnotí skutečný queue_log."""
import argparse
import csv
import fcntl
from html import escape
from itertools import product
import json
from math import ceil
import re
from pathlib import Path
from random import Random
from statistics import mean, median
import subprocess
import sys
import time
from uuid import uuid4

STRATEGIE = ("ringall", "leastrecent", "fewestcalls")
POCET = 6
ROZESTUP = 1.0
LOG = Path("/var/log/asterisk/queue_log")
LOG_SERIE = "queue_log_serie.txt"
KONFIG = Path("/etc/asterisk/lab-aktivni.conf")
SLOZKA = Path("/pokusy")
KONCE = {"COMPLETEAGENT", "COMPLETECALLER"}
SELHANI = {"IVRFAIL", "ABANDON", "EXITWITHTIMEOUT", "EXITEMPTY", "EXITWITHKEY"}


def cli(prikaz):
    p = subprocess.run(["asterisk", "-rx", prikaz], text=True, capture_output=True, timeout=12)
    vystup = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", p.stdout + p.stderr).strip()
    if p.returncode or "No such command" in vystup or "Unable to connect" in vystup:
        raise RuntimeError(f"{prikaz}\n{vystup}")
    return vystup


def priprav_frontu(fronta, strategie, agenti):
    config = [f"[{fronta}]", f"strategy = {strategie}", "ringinuse = no",
              "retry = 1", "timeout = 6", "wrapuptime = 0",
              "joinempty = yes", "leavewhenempty = no"]
    for agent in range(101, 101 + agenti):
        cli(f"devstate change Custom:agent{agent} NOT_INUSE")
        config.append(f"member => Local/{agent}@agenti/n,0,A{agent},Custom:agent{agent}")
    obsah = "\n".join(config) + "\n"
    KONFIG.write_text(obsah)
    cli("module reload app_queue.so")
    cli(f"dialplan set global LABQUEUE {fronta}")
    for _ in range(30):
        stav = cli(f"queue show {fronta}")
        if strategie in stav and all(f"Local/{n}@agenti/n" in stav for n in range(101, 101 + agenti)):
            return obsah, stav
        time.sleep(0.2)
    raise RuntimeError(f"Frontu se nepodařilo načíst.\n{stav}")


def radky_od(offset, fronta):
    if not LOG.exists():
        return []
    with LOG.open("rb") as f:
        f.seek(offset)
        radky = f.read().decode("utf-8", errors="replace").splitlines()
    return [r for r in radky if len(r.split("|")) >= 5 and r.split("|")[2] == fronta]


def zpracuj(radky, strategie, agenti, hloubka, opak, beh):
    hovory = {}
    for radek in radky:
        cas, uid, _, agent, udalost, *data = radek.split("|")
        zaznam = hovory.setdefault(uid, {})
        if udalost in zaznam and udalost in {"LABSTART", "CONNECT", "ENTERQUEUE"}:
            raise ValueError(f"Duplicitní událost {udalost} u {uid}")
        zaznam[udalost] = (int(cas), agent, data)
    vysledky = []
    for uid, ev in hovory.items():
        if "LABSTART" not in ev:
            continue
        if not (KONCE & ev.keys()) or SELHANI & ev.keys():
            raise ValueError(f"Nedokončený hovor {uid}. Podívej se do logu běhu.")
        if ev["MENU1"][2][0] != "1" or ("MENU2" in ev) != (hloubka == 2):
            raise ValueError(f"Nesouhlasí průchod IVR u {uid}")
        if hloubka == 2 and ev["MENU2"][2][0] != "2":
            raise ValueError(f"Chybí správná druhá volba u {uid}")
        spojeni, clen, data = ev["CONNECT"]
        vstup = ev["LABSTART"][0]
        fronta_od = ev["ENTERQUEUE"][0]
        cislo = next((i for i in range(101, 101 + agenti) if str(i) in clen), None)
        if cislo is None:
            raise ValueError(f"Neznámý agent {clen}")
        vysledky.append(dict(beh=beh, opakovani=opak, strategie=strategie, agenti=agenti,
                             ivr=hloubka, id_hovoru=uid, agent=cislo,
                             pred_frontou_s=fronta_od - vstup, fronta_s=int(data[0]),
                             celkem_s=spojeni - vstup))
    if len(vysledky) != POCET:
        raise ValueError(f"Čekal jsem {POCET} hovorů, mám {len(vysledky)}")
    return vysledky


def zmer(cil, strategie, agenti, hloubka, opak, poradi):
    beh = f"{poradi:02}_{strategie}_a{agenti}_ivr{hloubka}_r{opak}"
    fronta = "q" + uuid4().hex[:10]
    raw = cil / "raw"
    soubor = raw / (beh + ".txt")
    config, stav = priprav_frontu(fronta, strategie, agenti)
    (raw / (beh + ".conf")).write_text(config)
    (raw / (beh + "_pred.txt")).write_text(stav + "\n")
    offset = LOG.stat().st_size if LOG.exists() else 0
    print(f"\n{beh}  |  queue show {fronta}", flush=True)
    cislo = 700 + hloubka
    zacatek = time.monotonic()
    for i in range(POCET):
        time.sleep(max(0, zacatek + i * ROZESTUP - time.monotonic()))
        cli(f"channel originate Local/{cislo}@vstup/n extension {cislo}@volajici")
        print(f"  Volající {i + 1}/{POCET}", flush=True)
    deadline = time.monotonic() + 105
    while True:
        radky = radky_od(offset, fronta)
        konce = [r.split("|")[4] for r in radky]
        if sum(e in KONCE for e in konce) >= POCET or SELHANI.intersection(konce) or time.monotonic() > deadline:
            break
        time.sleep(0.4)
    soubor.write_text("\n".join(radky) + "\n")
    (raw / (beh + "_po.txt")).write_text(cli(f"queue show {fronta}") + "\n")
    data = zpracuj(radky, strategie, agenti, hloubka, opak, beh)
    print("  Hotovo. Sekundy ve frontě:", [h["fronta_s"] for h in data], flush=True)
    return data


def uloz_csv(cesta, radky):
    with cesta.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(radky[0]), delimiter=";")
        w.writeheader()
        w.writerows(radky)


def souhrn(data):
    radky = []
    for s, a, d in product(STRATEGIE, (1, 2), (1, 2)):
        skupina = [r for r in data if (r["strategie"], r["agenti"], r["ivr"]) == (s, a, d)]
        if not skupina:
            continue
        cekani = [r["fronta_s"] for r in skupina]
        radky.append(dict(strategie=s, agenti=a, ivr=d, n=len(skupina),
                          prumer_fronta_s=round(mean(cekani), 2), median_fronta_s=median(cekani),
                          max_fronta_s=max(cekani),
                          prumer_pred_frontou_s=round(mean(r["pred_frontou_s"] for r in skupina), 2),
                          prumer_celkem_s=round(mean(r["celkem_s"] for r in skupina), 2),
                          prijal_101=sum(r["agent"] == 101 for r in skupina),
                          prijal_102=sum(r["agent"] == 102 for r in skupina)))
    return radky


def graf_cekani(cil, data):
    behy = {1: {}, 2: {}}
    for hovor in data:
        if hovor["strategie"] == "ringall" and hovor["ivr"] == 1:
            behy[hovor["agenti"]].setdefault(hovor["beh"], []).append(hovor["fronta_s"])
    if any(len(opakovani) != 2 for opakovani in behy.values()):
        return ""

    rady = [[mean(cekani) for cekani in zip(*opakovani.values(), strict=True)]
            for opakovani in behy.values()]
    krok = max(1, ceil(max(map(max, rady)) / 5))
    levy, pravy, horni, spodni = 62, 560, 82, 274

    def x(i):
        return levy + i * (pravy - levy) / (POCET - 1)

    def y(s):
        return spodni - (spodni - horni) * s / (5 * krok)

    sit = []
    for s in range(0, 6 * krok, krok):
        sit.append(f'<path d="M{levy} {y(s):.1f}H{pravy}" stroke="#d4dfe6"/>'
                   f'<text x="{levy - 9}" y="{y(s) + 4:.1f}" text-anchor="end">{s}</text>')
    cisla = "".join(f'<text x="{x(i):.1f}" y="296" text-anchor="middle">{i + 1}</text>'
                    for i in range(POCET))
    cary = []
    for rada, barva, oznaceni, legenda_x, preruseni in zip(
        rady, ("#0b5f87", "#a13d17"), ("1 agent", "2 agenti"), (62, 200), ("none", "6 4"), strict=True
    ):
        souradnice = " ".join(f"{x(i):.1f},{y(s):.1f}" for i, s in enumerate(rada))
        body = "".join(f'<circle cx="{x(i):.1f}" cy="{y(s):.1f}" r="4" fill="{barva}">'
                       f'<title>Volající {i + 1}, {oznaceni}: {s:g} s</title></circle>'
                       for i, s in enumerate(rada))
        cary.append(f'<path d="M{legenda_x} 53h26" stroke="{barva}" stroke-width="3" stroke-dasharray="{preruseni}"/>'
                    f'<text x="{legenda_x + 34}" y="58">{oznaceni}</text>'
                    f'<polyline points="{souradnice}" fill="none" stroke="{barva}" stroke-width="3" '
                    f'stroke-dasharray="{preruseni}"/>{body}')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 332" role="img" aria-labelledby="popis">
<title id="popis">Průměrná čekací doba podle pořadí volajícího: ringall, jedno menu</title>
<rect width="600" height="332" fill="white"/>
<g font-family="system-ui, sans-serif" font-size="14" fill="#173044">
<text x="{levy}" y="28" font-size="17" font-weight="bold">Čekací doba ve frontě [s]</text>
{''.join(sit)}
<path d="M{levy} {horni}V{spodni}H{pravy}" fill="none" stroke="#56707e"/>
{cisla}{''.join(cary)}
<text x="{(levy + pravy) / 2}" y="320" text-anchor="middle">Pořadí volajícího v běhu</text>
</g></svg>'''
    (cil / "cekani_ringall.svg").write_text(svg, encoding="utf-8")
    return ('<h2>Jeden a dva agenti při stejné zátěži</h2><figure style="margin:0;max-width:680px">'
            '<img src="cekani_ringall.svg" alt="Čekací doba volajících 1 až 6 pro jednoho a dva agenty" '
            'style="width:100%;height:auto">'
            '<figcaption>Strategie ringall, jedna úroveň IVR. Každý bod je průměr dvou opakování pro dané pořadí '
            'volajícího v běhu podle události LABSTART.</figcaption></figure>')


def stranka(cil, souhrny, data):
    graf = graf_cekani(cil, data)
    odkaz_na_log = ""
    if (cil / LOG_SERIE).is_file():
        odkaz_na_log = f' · <a href="{LOG_SERIE}">Souvislý log celé série</a>'
    nadpisy = ["Strategie", "Agentů", "Úrovně IVR", "Hovorů", "Fronta Ø [s]", "Fronta medián [s]",
               "Fronta max [s]", "Před frontou Ø [s]", "Do spojení Ø [s]", "Přijal 101", "Přijal 102"]
    tabulka = "<tr>" + "".join(f"<th>{s}</th>" for s in nadpisy) + "</tr>"
    for r in souhrny:
        tabulka += "<tr>" + "".join(f"<td>{escape(str(v)).replace('.', ',')}</td>" for v in r.values()) + "</tr>"
    beh = data[0]["beh"]
    uid = data[0]["id_hovoru"]
    radky = (cil / "raw" / (beh + ".txt")).read_text().splitlines()
    vzorek = "\n".join(r for r in radky if r.split("|")[1] == uid)
    odkazy = "".join(f'<li>{b} · <a href="raw/{b}.txt">queue_log</a> · <a href="raw/{b}.conf">fronta</a>'
                     f' · <a href="raw/{b}_pred.txt">stav před</a> · <a href="raw/{b}_po.txt">stav po</a></li>'
                     for b in dict.fromkeys(r["beh"] for r in data))
    html = f"""<!doctype html><html lang="cs"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Čekací doby – výsledky měření</title><style>body{{font:16px/1.6 system-ui;color:#173044;background:#f8fafc;max-width:1200px;margin:3rem auto;padding:0 1rem}}h1,h2{{line-height:1.2}}table{{border-collapse:collapse;font-variant-numeric:tabular-nums}}td,th{{padding:6px 9px;border:1px solid #bdcbd6}}th{{background:#dbe8ef}}div,pre{{overflow-x:auto}}pre{{background:#163045;color:white;padding:1rem}}a{{color:#075b81}}</style>
<h1>Čekací doby v Asterisku</h1><p>Dokončených hovorů: {len(data)}. Průměry a mediány zahrnují všechny dokončené hovory dané kombinace.
Čekací doba ve frontě je první údaj za CONNECT (holdtime). Čas do spojení měříme od LABSTART. Asterisk zaznamenává časy v celých sekundách.</p>
<div><table>{tabulka}</table></div><p><a href="hovory.csv">Jednotlivé hovory (CSV)</a> · <a href="souhrn.csv">Souhrn (CSV)</a> · <a href="metodika.json">Parametry a verze</a>{odkaz_na_log}</p>
{graf}
<h2>Jeden hovor od vstupu po zavěšení</h2><p>Běh {beh}, ID {escape(uid)}. Sloupce: čas Unix | ID hovoru | fronta | agent | událost | údaje události.</p>
<pre>{escape(vzorek)}</pre><h2>Původní výřezy a konfigurace jednotlivých běhů</h2><ul>{odkazy}</ul></html>"""
    (cil / "index.html").write_text(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    volby = parser.add_subparsers(dest="rezim", required=True)
    demo = volby.add_parser("demo", help="Jeden živý běh se šesti hovory")
    demo.add_argument("strategie", choices=STRATEGIE)
    demo.add_argument("agenti", choices=(1, 2), type=int)
    demo.add_argument("ivr", choices=(1, 2), type=int)
    volby.add_parser("serie", help="Všech 12 kombinací, každá dvakrát")
    a = parser.parse_args()
    SLOZKA.mkdir(exist_ok=True)
    with open("/tmp/mereni.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("Jiné měření ještě běží. Počkej na dokončení.")
        kanaly = cli("core show channels count")
        if not any(r.strip() == "0 active channels" for r in kanaly.splitlines()):
            sys.exit("V ústředně stále běží hovor. Zkus to později.\n" + kanaly)
        cil = SLOZKA / (a.rezim + "-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:4])
        (cil / "raw").mkdir(parents=True)
        print("Výstup na notebooku: pokusy/" + cil.name, flush=True)
        if a.rezim == "demo":
            plan = [(a.strategie, a.agenti, a.ivr, 1)]
        else:
            kombinace = list(product(STRATEGIE, (1, 2), (1, 2)))
            Random(8).shuffle(kombinace)
            plan = [(*k, 1) for k in kombinace] + [(*k, 2) for k in reversed(kombinace)]
        meta = dict(asterisk=cli("core show version"), cas_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    rezim=a.rezim, plan=plan, hovoru_na_beh=POCET, rozestup_s=ROZESTUP,
                    zvednuti_agenta_s={"101": 1, "102": 3}, obsluha_s=6,
                    klavesy_ivr=["1", "2"], prodleva_pred_volbou_s=2, stav="rozpracovano")
        (cil / "metodika.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        zacatek_logu = LOG.stat().st_size if LOG.exists() else 0
        data = []
        for i, (s, n, d, r) in enumerate(plan, 1):
            data.extend(zmer(cil, s, n, d, r, i))
            uloz_csv(cil / "hovory.csv", data)
        if a.rezim == "serie":
            with LOG.open("rb") as f:
                f.seek(zacatek_logu)
                (cil / LOG_SERIE).write_bytes(f.read())
        souhrny = souhrn(data)
        uloz_csv(cil / "souhrn.csv", souhrny)
        stranka(cil, souhrny, data)
        meta["stav"] = "dokonceno"
        (cil / "metodika.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"\nHOTOVO · {len(data)} hovorů · pokusy/{cil.name}/index.html", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as chyba:
        sys.exit(f"CHYBA: {chyba}\nZkontroluj docker compose logs --tail 120 pbx a poslední složku v pokusy/.")
