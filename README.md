# Call centrum na Asterisku – čekací doba

**Autoři:** Jáchym Košař, KOS0434  / Matěj Huďa, HUD0160
**Předmět:** VoIP I, VŠB-TUO  
**Téma:** 8 – Call centrum na Asterisku.

## Plán měření
Porovnám strategie ringall, leastrecent a fewestcalls při jednom a dvou agentech a při jedné a dvou úrovních IVR. U každé kombinace proběhnou dvě opakování po šesti hovorech. Z události CONNECT vyčtu čekání ve frontě. Z časových značek spočítám také dobu od vstupu do ústředny do spojení s agentem.

**Moje očekávání před měřením:** Očekávám, že při stejném počtu a rozestupech příchozích hovorů druhý agent zkrátí průměrnou dobu čekání ve frontě, protože dva hovory bude možné obsluhovat současně.

## Spuštění
V kořenové složce repozitáře se spuštěným Docker Desktopem pro linuxové kontejnery:

```powershell
docker compose up -d --build
docker compose exec pbx asterisk -rx "core show version"
docker compose exec pbx python3 /lab/mer.py demo ringall 1 1
```

Příkaz pro celou sérii:

```powershell
docker compose exec pbx python3 /lab/mer.py serie
```

V režimu demo jsou parametry postupně strategie, počet agentů a hloubka IVR. Výsledky jednotlivých spuštění vznikají ve složce `pokusy/`. Vybraná dokončená série pro dokumentaci patří do `dukazy/mereni/`. Soubor `queue_log_serie.txt` obsahuje původní souvislý výpis všech 24 běhů. Každý běh má samostatný výřez a nastavení také ve složce `raw`.

## Ruční SIP ukázka ve PhonerLite

Nejdřív spustím SIP kontejner a ověřím, že je účet 1001 v Asterisku zaregistrovaný:

```powershell
docker compose -f compose.sip.yaml up -d --build
docker compose -f compose.sip.yaml exec pbx-sip asterisk -rx "pjsip show aor 1001"
```

PhonerLite připojím přes UDP k `127.0.0.1:15060`. Použiju účet **1001** s heslem **VoIP-Demo-1001**, který slouží jen pro lokální ukázku. Zavolám na **702** a v hlasovém menu postupně stisknu **1** a **2**. Asterisk zařadí hovor do fronty `ukazka` a přidělí ho automatickému agentovi.

Průběh hovoru zachytím ve Wiresharku nebo pomocí `tcpdump` a uložím i výpis `queue_log`. Podle události `SIPCALLID` dohledám, které interní ID hovoru v Asterisku odpovídá hodnotě Call-ID v síťovém záznamu. Postup je v kapitole 12 souboru `START.html`, kde je i zachycení registrace a hovoru na 701. Graf čekání najdu v HTML reportu po dokončení měřicí série.

Po ukázce kontejner zastavím:

```powershell
docker compose -f compose.sip.yaml stop
```

## Dokumentace
https://magical-youtiao-2e251a.netlify.app/
