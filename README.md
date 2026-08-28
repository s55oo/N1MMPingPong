# PingPong – N1MM Logger+ UDP 12060 lučke

Prikaz, kateri računalnik/postaja v namrežju trenutno **oddaja**.
Posluša N1MM Logger+ zunanji UDP broadcast (XML) na vratih **12060**
in prikaže sporočilo odtisnjene funkcijske tipke v barvi tiste postaje.

Obstajata dve orodji:

| Datoteka | Kaj je |
|---|---|
| `n1mm_lamps.py` / `lucke.exe` | **Lučka** – majhno okno, ki sveti v barvi postaje na oddaji |
| `n1mm_watch.py` / `PingPong.bat` | **Opazovalec** – podrobni dnevnik vseh paketov (debug/analiza) |

---

## 1. N1MM Logger+ nastavitev

1. V vsakem računalniku N1MM Logger+: **File → Settings → Configurer →
   External Broadcast**, izberi poročila **RadioInfo**, **ContactInfo**,
   **Spot**, **AppInfo**, **dynamicresults** …
2. **Broadcast Address**: `192.168.0.255` (broadcast celega omrežja, da
   vsak PC vidi vse postaje).
3. **Broadcast Port**: `12060` (privzeto, nezamenjaj).
4. Kot je poročil N1MM: **“Broadcast Data” je treba uporabiti tudi na
   ostalih računalnikih** (npr. Run2), drugače ne pošiljajo ničesar.

> N1MM pošilja samo **naslov** tipke (npr. `F1 CQ`), ne razširjenega
> CW/besedila. `Frekvenca` v `RadioInfo` je v desetinah Hz:
> Freq=701500 pomeni 7015,00 kHz.

---

## 2. Lučka (n1mm_lamps.py / lucke.exe)

### Vedenje

- Okno sveti **natanko toliko časa, kot postaja res oddaja**:
  pri `IsTransmitting=True` se prižge, pri `False` takoj ugasne.
- V oknu se prikaže besedilo zadnje pritisnjene funkcijske tipke
  (predpona `Fx` se izpusti).
- Okno je vedno na vrhu (`topmost`).

### Skupinski ključ (pas + mode)

Okno prikazuje **samo oddaje postaj na istem pasu in modeu kot lokalni
računalnik** (tisti, kjer okno teče):

- Pasovi: **1.8, 3.5, 7, 14, 21, 28** MHz
- Modei: **CW, RTTY, USB, LSB**

Primer: PC1, PC2, PC3 na 14 MHz CW → barve se lepo menjajo. Če je
PC4 na npr. 7 MHz CW, njegove oddaje **nikoli ne bodo** prikazane v
oknu, in obratno.

- Ključ se samodejno spreminja, ko lokalna postaja zamenja pas/mode
  (v nogi okna piše `spremljam 14 CW`).
- Postaja v temi nobenega od teh pasov/modeov se ne prikazuje.

### Zagon

```bat
        dvojni klik:  lucke.exe     (samostojna programska datoteka)
  ali:  dvojni klik:  Lampice.bat   (zažene pythonw n1mm_lamps.py)
```

Argumenti:

```bat
python n1mm_lamps.py [--port 12060] [--stale 5.0] [--config lamps.cfg]
```

- `--port` – vrata UDP (privzeto 12060).
- `--stale` – varnostna rezerva v sekundah; če od oddajajoče postaje
  v tem času ni nobenega paketa, lučka ugasne (privzeto 5.0).
- `--config` – pot do `lamps.cfg`.

### lamps.cfg (opcijsko)

Samodejno zaznavanje postaj deluje **tudi brez** te datoteke. Če hočeš
stabilne oznake/barve, vsaka vrstica pomeni:

```
IP,oznaka,barva
```

Primer:

```
192.168.0.77,PC1,red
192.168.0.69,Run2,green
```

- Vrstice s `#` se preskočijo.
- Če `lamps.cfg` ne obstaja ali ne vsebuje postaje, nova postaja dobi
  oznako iz paketa in barvo iz samodejne palete.
- `lucke.exe` bere `lamps.cfg` iz **iste mape, kjer je exe**.

---

## 3. Opazovalec (n1mm_watch.py)

Podroben vpogled v ves UDP promet: tabela postaj levo, promet desno,
podrobnosti/hex izbranega paketa spodaj.

```bat
        dvojni klik:  PingPong.bat
```

```bat
python n1mm_watch.py [--port 12060] [--bind 0.0.0.0] [--log FILE] [--selftest]
```

- Gumb **Ustavi/Poslušaj** – preklapljanje med poslušanjem.
- **Zamrzni** – preneha dodajati vrstice (promet še teče).
- **Avtopomik** – sledenje zadnjem paketu.
- **Hex/surovo** – podrobnosti paketa kot hex + surovi XML.
- **Zapisuj v datoteko** – JSON vrstice v `n1mm_pingpong.log`
  (privzeta lokacija poleg skripte; povej z `--log`).
- **Počisti** – izprazni prikaz.
- `--selftest` – preveri razčlenjevanje s testnimi paketi.

---

## 4. Gradnja samostojnega EXE (pokriti PC)

Potrebuje Python + PyInstaller:

```bat
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --name lucke --manifest manifest.xml n1mm_lamps.py
copy /Y dist\lucke.exe lucke.exe
```

`manifest.xml` poskrbi, da ima okno moderne kontrole (common controls).
Rezultat je ena datoteka `lucke.exe` (brez Python znamke) – prekopiraj
jo na ostale računalnike skupaj z (opcijskim) `lamps.cfg`.

---

## 5. Datoteke

```
n1mm_lamps.py    – lučka (izvorna koda)
lucke.exe        – lučka (samostojna, brez Pythona)
Lampice.bat      – zaganjalnik lučke (pythonw)
lamps.cfg        – opcijsko: IP,oznaka,barva
n1mm_watch.py    – opazovalec prometa
PingPong.bat     – zaganjalnik opazovalca
manifest.xml     – manifest za PyInstaller (common controls)
lucke.spec       – nastavitve zadnje gradnje PyInstaller
dist\            – izhod PyInstaller (aktualni lucke.exe)
```