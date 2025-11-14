#!/usr/bin/env python3
# ---
# File: calibra_sensore.py
# Directory: utils/
# Ultima Modifica: 2025-11-14
# Versione: 1.09
# ---

"""
SCRIPT: CALIBRAZIONE MANUALE (Ambiente Reale)

V 1.09:
- Aggiunta Opzione 6 per configurare il GAIN (sensibilità)
  del sensore (1, 4, 16, 60).
- Default GAIN impostato a 4x per ridurre il rumore di fondo.
- Salva e Esci ora sono 8 e 9.
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
CAMPIONI_PER_LETTURA = 10
VALID_GAINS = [1, 4, 16, 60]

dati_calibrazione_temporanei = {}
stop_live_thread = threading.Event()


# --- Inizializzazione Hardware ---
def inizializza_sensore():
    print("🔧 Inizializzazione sensore TCS34725...")
    integration_time = dati_calibrazione_temporanei.get('integration_time', 250)
    # --- MODIFICA V 1.09: Usa 4 come default per il gain ---
    gain = dati_calibrazione_temporanei.get('gain', 4)

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        sensor.integration_time = integration_time

        # Mappa il valore numerico del gain all'impostazione della libreria
        if gain == 1:
            sensor.gain = adafruit_tcs34725.GAIN_1X
        elif gain == 4:
            sensor.gain = adafruit_tcs34725.GAIN_4X
        elif gain == 16:
            sensor.gain = adafruit_tcs34725.GAIN_16X
        elif gain == 60:
            sensor.gain = adafruit_tcs34725.GAIN_60X
        else:
            print(f"   ⚠️ Gain {gain} non valido, imposto 4x.")
            sensor.gain = adafruit_tcs34725.GAIN_4X
            dati_calibrazione_temporanei['gain'] = 4  # Corregge il config
            gain = 4

        print(f"✅ Sensore inizializzato (Time: {integration_time}ms, Gain: {gain}x).")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725.")
        print(f"   Dettagli: {e}")
        return None


# --- Funzioni di Lettura ---
def leggi_rgb_attuale(sens):
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


def leggi_rgb_stabilizzato(sensor, campioni=CAMPIONI_PER_LETTURA):
    tot_r, tot_g, tot_b, letture_valide = 0, 0, 0, 0
    print(f"   Avvio campionamento ({campioni} letture)...")
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
    print("\n   ...Campionamento completato.")
    if letture_valide == 0: return {"R": 0, "G": 0, "B": 0}
    avg_r = int(tot_r / letture_valide)
    avg_g = int(tot_g / letture_valide)
    avg_b = int(tot_b / letture_valide)
    return {"R": avg_r, "G": avg_g, "B": avg_b}


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

    # --- MODIFICA V 1.09: Stato Gain ---
    gain = dati_calibrazione_temporanei.get('gain', 4)  # Default 4
    stato_gain = f"✅ IMPOSTATO ({gain}x)"
    # --- FINE MODIFICA ---

    debug_logging = dati_calibrazione_temporanei.get('debug_logging', False)
    stato_debug = "✅ ABILITATO" if debug_logging else "❌ DISABILITATO"

    print(f"1. Campiona 'Verde' (luce fissa o lampeggiante)  {stato_verde}")
    print(f"2. Campiona 'Rosso' (luce fissa o lampeggiante)  {stato_rosso}")
    print(f"3. Campiona 'Spento' (fisso)                     {stato_buio}")
    print("-" * 55)
    print(f"4. Imposta ID Macchina (per MQTT)                  {stato_id}")
    print(f"5. Imposta Tempo Integrazione (Sensore)          {stato_integrazione}")
    # --- MODIFICA V 1.09: Nuova Opzione 6 (Gain) ---
    print(f"6. Imposta Gain Sensore (Sensibilità)            {stato_gain}")
    print(f"7. Abilita/Disabilita Log di Debug                 {stato_debug}")
    print("-" * 55)
    # --- MODIFICA V 1.09: Opzioni 8 e 9 ---
    print("8. Salva calibrazione e configurazione su file ed Esci")
    print("9. Esci SENZA salvare")
    print("=" * 55)


def salva_file_calibrazione():
    """Controlla e salva i dati di calibrazione."""
    print("\n--- RIEPILOGO CONFIGURAZIONE ---")

    if "integration_time" not in dati_calibrazione_temporanei:
        print("   ℹ️ 'Tempo Integrazione' non impostato, imposto il default: 250ms.")
        dati_calibrazione_temporanei["integration_time"] = 250

    # --- MODIFICA V 1.09: Imposta default Gain ---
    if "gain" not in dati_calibrazione_temporanei:
        print("   ℹ️ 'Gain' non impostato, imposto il default: 4x.")
        dati_calibrazione_temporanei["gain"] = 4
    # --- FINE MODIFICA ---

    if "debug_logging" not in dati_calibrazione_temporanei:
        dati_calibrazione_temporanei["debug_logging"] = False

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
    # --- MODIFICA V 1.09: Stampa Gain ---
    print(f"  Gain Sensore:      {dati_calibrazione_temporanei.get('gain', 'N/D')}x")
    print(
        f"  Log di Debug:      {'Abilitato' if dati_calibrazione_temporanei.get('debug_logging') else 'Disabilitato'}")
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

        # --- MODIFICA V 1.09: Range 1-9 ---
        scelta = input("Inserisci la tua scelta (1-9): ")

        if scelta == '1':
            print("\n--- 1. Campiona VERDE ---")
            print("Ora fai in modo che la macchina mostri la luce VERDE.")
            input("Quando è pronta, premi INVIO per avviare il campionamento...")
            valore = leggi_rgb_stabilizzato(sensor_main)
            dati_calibrazione_temporanei["verde"] = valore
            print(f"✅ 'Verde' registrato: {valore}")
            time.sleep(1)

        elif scelta == '2':
            print("\n--- 2. Campiona ROSSO ---")
            print("Ora fai in modo che la macchina mostri la luce ROSSA.")
            input("Quando è pronta, premi INVIO per avviare il campionamento...")
            valore = leggi_rgb_stabilizzato(sensor_main)
            dati_calibrazione_temporanei["non_verde"] = valore
            print(f"✅ 'Rosso' registrato: {valore}")
            time.sleep(1)

        elif scelta == '3':
            print("\n--- 3. Campiona SPENTO ---")
            print("Ora fai in modo che la macchina spenga la luce (stato BUIO).")
            print("CONSIGLIO: Copri il sensore per bloccare la luce ambientale.")
            input("Quando è pronta, premi INVIO per avviare il campionamento...")
            valore = leggi_rgb_stabilizzato(sensor_main)
            dati_calibrazione_temporanei["buio"] = valore
            print(f"✅ 'Spento' registrato: {valore}")
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

        # --- MODIFICA V 1.09: Nuova Opzione 6 (Gain) ---
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
        # --- FINE MODIFICA ---

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

        elif scelta == '8':  # Salva
            if salva_file_calibrazione():
                break  # Esce dal loop

        elif scelta == '9':  # Esci
            print("\nUscita senza salvataggio.")
            break  # Esce dal loop

        else:
            print(f"Scelta non valida. Inserisci un numero da 1 a 9.")
            time.sleep(1)

    print("Programma di calibrazione terminato.")


if __name__ == "__main__":
    main()