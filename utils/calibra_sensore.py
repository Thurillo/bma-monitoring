#!/usr/bin/env python3
# ---
# File: calibra_sensore.py
# Directory: utils/
# Ultima Modifica: 2025-11-14
# Versione: 1.15
# ---

"""
SCRIPT: CALIBRAZIONE MANUALE (Ambiente Reale)

V 1.15:
- Aggiunta Opzione 8 per configurare 'buffer_size'.
- 'buffer_size' viene salvato in calibrazione.json.
- Valore di default per 'buffer_size' è 35.
- Spostati Salva/Esci a 9/10.
"""

import board
import busio
import adafruit_tcs34725
import time
import json
import sys
import os
import threading

# --- Parametri ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
FILE_CALIBRAZIONE = os.path.join(CONFIG_DIR, "calibrazione.json")

# Per la media (stato SPENTO)
CAMPIONI_PER_MEDIA = 10
# Per il picco (stati VERDE/ROSSO)
DURATA_CAMPIONAMENTO_PICCO_SEC = 10  # Campiona per 10 secondi
# Per filtrare il buio durante il picco, una lettura deve essere
# almeno a questa "distanza" dal valore SPENTO.
DISTANZA_MINIMA_DA_SPENTO = 10.0

VALID_GAINS = [1, 4, 16, 60]

dati_calibrazione_temporanei = {}
stop_live_thread = threading.Event()


# --- Funzioni di Distanza ---
def calcola_distanza_rgb_raw(rgb1_tuple, rgb2_dict):
    """Calcola distanza tra una tupla (lettura) e un dict (calibrazione)."""
    r1, g1, b1 = rgb1_tuple
    r2, g2, b2 = rgb2_dict['R'], rgb2_dict['G'], rgb2_dict['B']
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


# --- Inizializzazione Hardware ---
def inizializza_sensore():
    print("🔧 Inizializzazione sensore TCS34725...")
    integration_time = dati_calibrazione_temporanei.get('integration_time', 250)
    gain = dati_calibrazione_temporanei.get('gain', 4)

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        sensor.integration_time = integration_time

        if gain in VALID_GAINS:
            sensor.gain = gain
        else:
            print(f"   ⚠️ Gain {gain} non valido, imposto 4x.")
            sensor.gain = 4
            dati_calibrazione_temporanei['gain'] = 4
            gain = 4

        print(f"✅ Sensore inizializzato (Time: {integration_time}ms, Gain: {gain}x).")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725.")
        print(f"   Dettagli: {e}")
        return None


# --- Funzioni di Lettura ---
def leggi_rgb_attuale(sens):
    """Legge la tupla (R, G, B) attuale."""
    try:
        result = sens.color_rgb_bytes
        if len(result) >= 3: return result[:3]
    except Exception:
        pass
    try:
        raw = sens.color_raw
        return min(255, int(raw[0] / 256)), min(255, int(raw[1] / 256)), min(255, int(raw[2] / 256))
    except Exception:
        return 0, 0, 0


def leggi_rgb_media(sensor, campioni=CAMPIONI_PER_MEDIA):
    """Calcola la MEDIA di N campioni (per lo stato SPENTO)."""
    tot_r, tot_g, tot_b, letture_valide = 0, 0, 0, 0
    print(f"   Avvio campionamento MEDIA ({campioni} letture)...")
    for i in range(campioni):
        r, g, b = 0, 0, 0
        try:
            r, g, b = leggi_rgb_attuale(sensor)
            letture_valide += 1;
            tot_r += r;
            tot_g += g;
            tot_b += b
            print(f"   Lettura {i + 1}/{campioni}: R={r:<3} G={g:<3} B={b:<3}", end="\r")
        except Exception:
            print(f"   Lettura {i + 1}/{campioni}: FALLITA")
        time.sleep(0.05)
    print("\n   ...Campionamento MEDIA completato.")
    if letture_valide == 0: return {"R": 0, "G": 0, "B": 0}
    return {
        "R": int(tot_r / letture_valide),
        "G": int(tot_g / letture_valide),
        "B": int(tot_b / letture_valide)
    }


# --- MODIFICA V 1.14: Logica Picco CORRETTA ---
def leggi_rgb_picco(sensor, valore_spento_dict, colore_target):
    """
    Calcola il PICCO di N letture (per stati VERDE/ROSSO lampeggianti).
    Trova la lettura (singola, non media) più LONTANA dal valore SPENTO
    in un intervallo di 10 secondi.
    """

    picco_rgb_tuple = (0, 0, 0)
    # Salva il valore R o G più alto trovato
    picco_valore_canale = -1

    # Calcola il tempo di pausa in base all'integration time
    integration_time_sec = sensor.integration_time / 1000.0  # in secondi
    # Il ciclo deve includere la lettura (integration_time) + una piccola pausa I2C/Python
    pausa_ciclo = max(0.05, integration_time_sec + 0.01)

    numero_campioni_totali = int(DURATA_CAMPIONAMENTO_PICCO_SEC / pausa_ciclo)

    print(f"   Avvio campionamento PICCO ({DURATA_CAMPIONAMENTO_PICCO_SEC} sec, ~{numero_campioni_totali} letture)...")
    print(f"   Cerco il valore {colore_target} più alto...")

    for i in range(numero_campioni_totali):
        # Legge il valore attuale singolo (NON la media)
        lettura_tuple = leggi_rgb_attuale(sensor)

        distanza = calcola_distanza_rgb_raw(lettura_tuple, valore_spento_dict)

        # Stampa diagnostica
        print(
            f"   Campionamento {i + 1}/{numero_campioni_totali}: R={lettura_tuple[0]:<3} G={lettura_tuple[1]:<3} B={lettura_tuple[2]:<3} (Dist: {distanza:<5.1f})",
            end="\r")

        # FILTRO: Ignora le letture troppo vicine a SPENTO (è il buio del lampeggio)
        if distanza > DISTANZA_MINIMA_DA_SPENTO:

            if colore_target == "VERDE":
                valore_canale_corrente = lettura_tuple[1]  # Canale G
            else:  # colore_target == "ROSSO"
                valore_canale_corrente = lettura_tuple[0]  # Canale R

            # Controlla se questo è il valore (R o G) più alto trovato finora
            if valore_canale_corrente > picco_valore_canale:
                picco_valore_canale = valore_canale_corrente
                picco_rgb_tuple = lettura_tuple

        time.sleep(pausa_ciclo)  # Attende il tempo calcolato

    print("\n   ...Campionamento PICCO completato.")
    if picco_valore_canale == -1:
        print("   ⚠️ ATTENZIONE: Nessuna lettura valida trovata (troppo scuro?). Salvataggio (0,0,0).")
    return {"R": picco_rgb_tuple[0], "G": picco_rgb_tuple[1], "B": picco_rgb_tuple[2]}


# --- FINE MODIFICA V 1.14 ---


def debug_lettura_live_thread():
    sensor_thread = inizializza_sensore()
    if not sensor_thread:
        print("   [LIVE] Errore: sensore non trovato nel thread.")
        return

    print("   [LIVE] Avvio lettura live... (si aggiornerà sotto il menu)")
    while not stop_live_thread.is_set():
        try:
            rgb = leggi_rgb_attuale(sensor_thread)
            print(f"   [LIVE] Lettura: R={rgb[0]:<3} G={rgb[1]:<3} B={rgb[2]:<3}   ", end="\r")
            time.sleep(0.3)
        except Exception:
            time.sleep(1)
    print("\n   [LIVE] Lettura live fermata.                ")


# --- Funzioni Menu ---
def carica_dati_esistenti():
    global dati_calibrazione_temporanei
    try:
        if os.path.exists(FILE_CALIBRAZIONE):
            with open(FILE_CALIBRAZIONE, 'r') as f:
                dati_calibrazione_temporanei = json.load(f)
            print(f"✅ Dati di calibrazione precedenti caricati da '{FILE_CALIBRAZIONE}'")
        else:
            print("ℹ️ Nessun file di calibrazione esistente trovato. Si parte da zero.")
    except Exception as e:
        print(f"⚠️ Errore nel caricare 'calibrazione.json': {e}. Si parte da zero.")
        dati_calibrazione_temporanei = {}


def format_rgb(valore):
    if not isinstance(valore, dict) or not all(k in valore for k in ('R', 'G', 'B')):
        return "N/D"
    return f"R:{valore['R']} G:{valore['G']} B:{valore['B']}"


def stampa_menu():
    """Mostra il menu delle opzioni e lo stato della calibrazione."""
    print("\n" + "=" * 55)
    print("--- MENU CALIBRAZIONE SENSORE E CONFIGURAZIONE ---")
    print("=" * 55)

    # Colori
    v = dati_calibrazione_temporanei.get('verde')
    stato_verde = f"✅ CALIBRATO ({format_rgb(v)})" if v else "❌ DA FARE"

    r = dati_calibrazione_temporanei.get('non_verde')
    stato_rosso = f"✅ CALIBRATO ({format_rgb(r)})" if r else "❌ DA FARE"

    b = dati_calibrazione_temporanei.get('buio')
    stato_buio = f"✅ CALIBRATO ({format_rgb(b)})" if b else "❌ DA FARE"

    # ID Macchina
    machine_id = dati_calibrazione_temporanei.get('machine_id')
    stato_id = f"✅ IMPOSTATO ({machine_id})" if machine_id else "❌ DA IMPOSTARE"

    # Tempo di Integrazione
    integration_time = dati_calibrazione_temporanei.get('integration_time', 250)
    stato_integrazione = f"✅ IMPOSTATO ({integration_time}ms)"

    gain = dati_calibrazione_temporanei.get('gain', 4)  # Default 4
    stato_gain = f"✅ IMPOSTATO ({gain}x)"

    debug_logging = dati_calibrazione_temporanei.get('debug_logging', False)
    stato_debug = "✅ ABILITATO" if debug_logging else "❌ DISABILITATO"

    # --- MODIFICA V 1.15 ---
    buffer_size = dati_calibrazione_temporanei.get('buffer_size', 35)  # Default 35
    stato_buffer = f"✅ IMPOSTATO ({buffer_size} letture)"
    # --- FINE MODIFICA V 1.15 ---

    print(f"1. Campiona 'Verde' (PICCO luce)                 {stato_verde}")
    print(f"2. Campiona 'Rosso' (PICCO luce)                 {stato_rosso}")
    print(f"3. Campiona 'Spento' (MEDIA buio)                {stato_buio}")
    print("-" * 55)
    print(f"4. Imposta ID Macchina (per MQTT)                  {stato_id}")
    print(f"5. Imposta Tempo Integrazione (Sensore)          {stato_integrazione}")
    print(f"6. Imposta Gain Sensore (Sensibilità)            {stato_gain}")
    print(f"7. Abilita/Disabilita Log di Debug                 {stato_debug}")
    # --- MODIFICA V 1.15 ---
    print(f"8. Imposta Buffer Size (Stabilità Monitor)       {stato_buffer}")
    print("-" * 55)
    print("9. Salva calibrazione e configurazione su file ed Esci")
    print("10. Esci SENZA salvare")
    print("=" * 55)
    # --- FINE MODIFICA V 1.15 ---


def salva_file_calibrazione():
    """Controlla e salva i dati di calibrazione."""
    print("\n--- RIEPILOGO CONFIGURAZIONE ---")

    if "integration_time" not in dati_calibrazione_temporanei:
        print("   ℹ️ 'Tempo Integrazione' non impostato, imposto il default: 250ms.")
        dati_calibrazione_temporanei["integration_time"] = 250

    if "gain" not in dati_calibrazione_temporanei:
        print("   ℹ️ 'Gain' non impostato, imposto il default: 4x.")
        dati_calibrazione_temporanei["gain"] = 4

    if "debug_logging" not in dati_calibrazione_temporanei:
        dati_calibrazione_temporanei["debug_logging"] = False

        # --- MODIFICA V 1.15 ---
    if "buffer_size" not in dati_calibrazione_temporanei:
        print("   ℹ️ 'Buffer Size' non impostato, imposto il default: 35.")
        dati_calibrazione_temporanei["buffer_size"] = 35
    # --- FINE MODIFICA V 1.15 ---

    mancanti = []
    if "verde" not in dati_calibrazione_temporanei: mancanti.append("Verde")
    if "non_verde" not in dati_calibrazione_temporanei: mancanti.append("Rosso")
    if "buio" not in dati_calibrazione_temporanei: mancanti.append("Spento")
    if "machine_id" not in dati_calibrazione_temporanei: mancanti.append("ID Macchina")

    # Stampa valori impostati
    print(f"  Verde:             {format_rgb(dati_calibrazione_temporanei.get('verde'))}")
    print(f"  Rosso (non_verde): {format_rgb(dati_calibrazione_temporanei.get('non_verde'))}")
    print(f"  Spento (buio):     {format_rgb(dati_calibrazione_temporanei.get('buio'))}")
    print(f"  ID Macchina:       {dati_calibrazione_temporanei.get('machine_id', 'N/D')}")
    print(f"  Tempo Integrazione: {dati_calibrazione_temporanei.get('integration_time', 'N/D')}ms")
    print(f"  Gain Sensore:      {dati_calibrazione_temporanei.get('gain', 'N/D')}x")
    print(
        f"  Log di Debug:      {'Abilitato' if dati_calibrazione_temporanei.get('debug_logging') else 'Disabilitato'}")
    # --- MODIFICA V 1.15 ---
    print(f"  Buffer Size:       {dati_calibrazione_temporanei.get('buffer_size', 'N/D')} letture")
    # --- FINE MODIFICA V 1.15 ---
    print("-" * 36)

    conferma = 'n'
    if mancanti:
        print(f"⚠️ ATTENZIONE: I seguenti valori non sono impostati:")
        for m in mancanti: print(f"   - {m}")
        conferma = input(f"Salvare comunque? (s/n): ").lower()
    else:
        conferma = input(f"Tutti i valori sono impostati. Salvare? (s/n): ").lower()

    if conferma == 's':
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(FILE_CALIBRAZIONE, 'w') as f:
                json.dump(dati_calibrazione_temporanei, f, indent=4)
            print(f"\n✅ Dati salvati con successo in '{FILE_CALIBRAZIONE}'!")
            return True
        except Exception as e:
            print(f"\n❌ ERRORE durante il salvataggio del file: {e}")
            return False
    else:
        print("   Salvataggio annullato.")
        return False


# --- Ciclo Principale ---
def main():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    carica_dati_esistenti()

    sensor_main = inizializza_sensore()
    if not sensor_main:
        print("Impossibile procedere. Controlla hardware.")
        return

    print("\nIMPORTANTE: Posiziona il sensore in modo che 'veda' le luci.")

    while True:
        stop_live_thread.clear()
        live_thread = threading.Thread(target=debug_lettura_live_thread, daemon=True)
        live_thread.start()

        stampa_menu()

        try:
            time.sleep(3)
        except KeyboardInterrupt:
            stop_live_thread.set();
            live_thread.join();
            print("\nUscita.");
            break

        stop_live_thread.set()
        live_thread.join(timeout=1.0)

        # --- MODIFICA V 1.15 ---
        scelta = input("Inserisci la tua scelta (1-10): ")

        if scelta == '1' or scelta == '2':  # VERDE o ROSSO (PICCO)
            # --- FINE MODIFICA V 1.15 ---
            # --- Logica V 1.12 ---
            if 'buio' not in dati_calibrazione_temporanei:
                print("\n❌ ERRORE: Devi calibrare 'Spento' (Opzione 3) PRIMA di calibrare Verde o Rosso.")
                time.sleep(2)
                continue  # Torna al menu

            valore_spento = dati_calibrazione_temporanei['buio']

            if scelta == '1':
                print("\n--- 1. Campiona PICCO VERDE ---")
                print("Ora fai in modo che la macchina mostri la luce VERDE (anche lampeggiante).")
                input(
                    f"Quando è pronta, premi INVIO per avviare il campionamento ({DURATA_CAMPIONAMENTO_PICCO_SEC} sec)...")
                # --- MODIFICA V 1.14 ---
                valore = leggi_rgb_picco(sensor_main, valore_spento, "VERDE")
                dati_calibrazione_temporanei["verde"] = valore
                print(f"✅ 'Verde' (Picco) registrato: {valore}")

            else:  # scelta == '2'
                print("\n--- 2. Campiona PICCO ROSSO ---")
                print("Ora fai in modo che la macchina mostri la luce ROSSA (anche lampeggiante).")
                input(
                    f"Quando è pronta, premi INVIO per avviare il campionamento ({DURATA_CAMPIONAMENTO_PICCO_SEC} sec)...")
                # --- MODIFICA V 1.14 ---
                valore = leggi_rgb_picco(sensor_main, valore_spento, "ROSSO")
                dati_calibrazione_temporanei["non_verde"] = valore
                print(f"✅ 'Rosso' (Picco) registrato: {valore}")

            time.sleep(1)

        elif scelta == '3':  # SPENTO (MEDIA)
            print("\n--- 3. Campiona MEDIA SPENTO ---")
            print("Ora fai in modo che la macchina spenga la luce (stato BUIO).")
            print("CONSIGLIO: Copri il sensore per bloccare la luce ambientale.")
            input("Quando è pronta, premi INVIO per avviare il campionamento (MEDIA)...")
            valore = leggi_rgb_media(sensor_main, campioni=CAMPIONI_PER_MEDIA)
            dati_calibrazione_temporanei["buio"] = valore
            print(f"✅ 'Spento' (Media) registrato: {valore}")
            time.sleep(1)

        elif scelta == '4':
            print("\n--- 4. Imposta ID Macchina (MQTT) ---")
            current_id = dati_calibrazione_temporanei.get('machine_id', 'N/D')
            print(f"   ID Attuale: {current_id}")
            nuovo_id = input("   Inserisci il nuovo ID Macchina: ")
            if nuovo_id:
                dati_calibrazione_temporanei["machine_id"] = nuovo_id
                print(f"✅ ID Macchina impostato su: '{nuovo_id}'")
            else:
                print("   Nessuna modifica.")
            time.sleep(1)

        elif scelta == '5':
            print("\n--- 5. Imposta Tempo Integrazione (ms) ---")
            current_time = dati_calibrazione_temporanei.get('integration_time', 250)
            print(f"   Valore Attuale: {current_time}ms")
            print(f"   CONSIGLIO: 250 (Stabile), 150 (Veloce). Deve corrispondere al monitor.")
            try:
                nuovo_tempo_str = input(f"   Inserisci nuovo tempo (INVIO per {current_time}): ")
                if nuovo_tempo_str:
                    nuovo_tempo = int(nuovo_tempo_str)
                    dati_calibrazione_temporanei["integration_time"] = nuovo_tempo
                    print(f"✅ Tempo Integrazione impostato su: {nuovo_tempo}ms")
                    sensor_main = inizializza_sensore()
                else:
                    print("   Nessuna modifica.")
            except ValueError:
                print("   ❌ Errore: Inserisci solo un numero (es. 250).")
            time.sleep(1)

        elif scelta == '6':
            print("\n--- 6. Imposta Gain Sensore (Sensibilità) ---")
            current_gain = dati_calibrazione_temporanei.get('gain', 4)
            print(f"   Valore Attuale: {current_gain}x")
            print(f"   Valori validi: {VALID_GAINS}")
            print(f"   CONSIGLIO: 4 (Rumore basso), 16 (Standard).")
            try:
                nuovo_gain_str = input(f"   Inserisci nuovo gain (INVIO per {current_gain}): ")
                if nuovo_gain_str:
                    nuovo_gain = int(nuovo_gain_str)
                    if nuovo_gain in VALID_GAINS:
                        dati_calibrazione_temporanei["gain"] = nuovo_gain
                        print(f"✅ Gain impostato su: {nuovo_gain}x")
                        sensor_main = inizializza_sensore()
                    else:
                        print(f"   ❌ Errore: Valore non valido. Scegli tra {VALID_GAINS}.")
                else:
                    print("   Nessuna modifica.")
            except ValueError:
                print(f"   ❌ Errore: Inserisci solo un numero (es. 4).")
            time.sleep(1)

        elif scelta == '7':
            print("\n--- 7. Abilita/Disabilita Log di Debug ---")
            current_status = dati_calibrazione_temporanei.get('debug_logging', False)
            nuovo_stato = not current_status
            dati_calibrazione_temporanei["debug_logging"] = nuovo_stato
            if nuovo_stato:
                print("✅ Log di Debug ABILITATO.")
                print("   Lo script di monitoraggio scriverà nella cartella 'LOG/'.")
            else:
                print("❌ Log di Debug DISABILITATO.")
            time.sleep(1)

        # --- MODIFICA V 1.15 ---
        elif scelta == '8':
            print("\n--- 8. Imposta Buffer Size (Stabilità Monitor) ---")
            current_size = dati_calibrazione_temporanei.get('buffer_size', 35)
            print(f"   Valore Attuale: {current_size} letture")
            print(f"   CONSIGLIO: 35 (Stabile, default), 20 (Reattivo).")
            try:
                nuovo_size_str = input(f"   Inserisci nuovo buffer size (INVIO per {current_size}): ")
                if nuovo_size_str:
                    nuovo_size = int(nuovo_size_str)
                    if nuovo_size >= 10 and nuovo_size <= 200:
                        dati_calibrazione_temporanei["buffer_size"] = nuovo_size
                        print(f"✅ Buffer Size impostato su: {nuovo_size} letture")
                    else:
                        print("   ❌ Errore: Inserisci un numero tra 10 e 200.")
                else:
                    print("   Nessuna modifica.")
            except ValueError:
                print("   ❌ Errore: Inserisci solo un numero (es. 35).")
            time.sleep(1)

        elif scelta == '9':  # Salva
            if salva_file_calibrazione():
                break  # Esce dal loop

        elif scelta == '10':  # Esci
            print("\nUscita senza salvataggio.")
            break  # Esce dal loop

        else:
            print(f"Scelta non valida. Inserisci un numero da 1 a 10.")
            time.sleep(1)
        # --- FINE MODIFICA V 1.15 ---

    print("Programma di calibrazione terminato.")


if __name__ == "__main__":
    main()