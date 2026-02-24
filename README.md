# Maps3dGenerezion (MVP)

Programma desktop Python (Windows 10/11) per generare una mappa 3D stampabile (`.stl`) da traccia GPX + DEM locale.

## Avvio rapido

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m maps3d_app
```

## Backend

- **Blender (consigliato)**: qualità più alta, export multiplo, booleane complesse.
- **Python**: fallback semplice.

## Output “soluzione finale”

Con backend Blender l’export produce:

- `*_base_brown.stl` (terreno + base chiusa)
- `*_water.stl` (layer acqua da OSM; fallback procedurale se OSM non disponibile)
- `*_green.stl` (layer verde da OSM; fallback procedurale)
- `*_detail.stl` (dettagli, priorità strade principali OSM; fallback curve di dettaglio)
- `*_track_inlay_red.stl` (traccia rossa separata ad incastro)
- `*_frame.stl` (cornice separata incassata premium)

In **Test incastro 40×40** i nomi diventano `*_test_...stl`.

## Parametri UI aggiunti

- `AMS 4 colori mappa` (`ams_enabled`, default ON)
- `Traccia rossa a incastro` (`track_inlay_enabled`, default ON)
- `groove_width_mm` (default 2.6)
- `groove_depth_mm` (default 1.6)
- `groove_chamfer_mm` (default 0.4)
- `track_clearance_mm` (default 0.20)
- `track_relief_mm` (default 0.6)
- `track_top_radius_mm` (default 0.8)

## Workflow Bambu Studio (consigliato)

1. Importa `*_base_brown.stl`, `*_water.stl`, `*_green.stl`, `*_detail.stl` come **multi-part object**.
2. Assegna i 4 filamenti AMS (marrone/blu/verde/dettaglio).
3. Stampa `*_track_inlay_red.stl` separatamente in rosso.
4. Inserisci la traccia nella scanalatura: resta in rilievo di `track_relief_mm`.
5. Stampa `*_frame.stl` separata e monta il set completo.

## Cornice incassata (default)

La cornice resta in modalità **incassata premium** con:

- `recess_mm`: quota d’incasso sotto il bordo frontale
- `lead_in_mm`: chamfer invito
- `finger_notch_radius_mm`: scassi di estrazione (0=off)
- `rim_mm`: fascia piatta bordo mappa

## Profili stampante e clearance

- **Bambu**: clearance consigliata 0.20–0.25 mm
- **Voron**: clearance consigliata 0.25–0.30 mm
- **PETG**: aggiungere ~+0.05 mm rispetto al valore PLA

La UI include i profili preimpostati Bambu/Voron/Personalizzato.

## DEM automatico SRTM

1. Seleziona il file GPX.
2. Clicca **Scarica DEM (SRTM 30m)** (bbox GPX + margine 20%).
3. Quando il DEM è pronto, clicca **Genera STL**.

## Anteprima 3D embedded

La GUI include una preview 3D embedded (pyqtgraph OpenGL) dentro la finestra:
- carica gli STL generati (base, layer AMS, traccia, cornice)
- permette di centrare/zoomare automaticamente il modello
- mostra log operazioni e progresso generazione/download senza bloccare la UI

Note driver OpenGL:
- se vedi schermo nero, aggiorna i driver GPU
- in ambienti problematici prova esecuzione con renderer/software OpenGL del sistema
