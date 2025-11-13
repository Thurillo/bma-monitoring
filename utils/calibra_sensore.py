#!/usr/bin/env python3
# ---
# File: calibra_sensore.py
# Directory: utils/
# Ultima Modifica: 2025-11-13
# Versione: 1.02
# ---

"""
SCRIPT: CALIBRAZIONE MANUALE (Ambiente Reale)

Permette all'utente di:
1. Campionare gli stati VERDE, ROSSO (non_verde), SPENTO (buio).
2. Impostare l'ID Macchina (per MQTT).
3. Impostare il Tempo di Integrazione del sensore.
4. Salvare tutto in 'config/calibrazione.json'.

Supporta il caricamento dei dati esistenti per modifiche parziali.
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
CAMPIONI_PER_LETTURA = 10  # Numero di letture da mediare per un valore stabile

# Dizionario per tenere i valori (caricato all'avvio)
dati_calibrazione_temporanei = {}

# Variabili per il thread di lettura live
stop_live_thread = threading.Event()


# --- Inizializzazione Hardware ---

def inizializza_sensore():
    """
    Inizializza il sensore TCS34725 leggendo le impostazioni
    dalla configurazione globale (dati_calibrazione_temporanei).
    """
    print("🔧 Inizializzazione sensore TCS34725...")

    # Legge l'integration_time dalla config caricata, o usa 250 come default
    integration_time = dati_calibrazione_temporanei.get('integration_time', 250)

    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        # USARE LE STESSE IMPOSTAZIONI DELLO SCRIPT DI MONITORAGGIO
        sensor.integration_time = integration_time
        sensor.gain = 16
        print(f"✅ Sensore inizializzato (Time: {integration_time}ms, Gain: 16x).")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725.")
        print(f"   Dettagli: {e}")
        return None


# --- Funzioni di Lettura ---

def leggi_rgb_attuale(sens):
    """Esegue una singola lettura RGB, con fallback."""
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
    """Legge il sensore 'campioni' volte e restituisce i valori medi R, G, B."""
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
    """Mostra i valori letti dal sensore in tempo reale in un thread separato."""
    # Inizializza un sensore separato per il thread live
    # leggerà le impostazioni dalla config globale
    sensor_thread = inizializza_sensore()
    if not sensor_thread:
        print("   [LIVE] Errore: sensore non trovato nel thread.")
        return

    print("   [LIVE] Avvio lettura live... (si aggiornerà sotto il menu)")
    while not stop_live_thread.is_set():
        try:
            rgb = leggi_rgb_attuale(sensor_thread)
            # Stampa sulla stessa riga e torna all'inizio
            print(f"   [LIVE] Lettura: R={rgb[0]:<3} G={rgb[1]:<3} B={rgb[2]:<3}   ", end="\r")
            time.sleep(0.3)  # Aggiorna circa 3 volte al secondo
        except Exception:
            # Ignora errori momentanei
            time.sleep(1)
    print("\n   [LIVE] Lettura live fermata.                ")


# --- Funzioni Menu ---

def carica_dati_esistenti():
    """Carica i dati dal file JSON se esiste."""
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
    """Formatta i valori RGB per il menu."""
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
    stato_id = f"✅ IMPOSTATO ({machine_id})" if machine_id else "❌ NON IMPOSTATO"

    # NUOVO: Tempo di Integrazione
    integration_time = dati_calibrazione_temporanei.get('integration_time', 250)
    stato_integrazione = f"✅ IMPOSTATO ({integration_time}ms)"

    print(f"1. Campiona 'Verde' (luce fissa o lampeggiante)  {stato_verde}")
    print(f"2. Campiona 'Rosso' (luce fissa o lampeggiante)  {stato_rosso}")
    print(f"3. Campiona 'Spento' (fisso)                     {stato_buio}")
    print("-" * 55)
    print(f"4. Imposta ID Macchina (per MQTT)                  {stato_id}")
    print(f"5. Imposta Tempo Integrazione (Sensore)          {stato_integrazione}")
    print("-" * 55)
    print("6. Salva calibrazione e configurazione su file ed Esci")
    print("7. Esci SENZA salvare")
    print("=" * 55)


def salva_file_calibrazione():
    """Controlla e salva i dati di calibrazione."""
    print("\n--- RIEPILOGO CONFIGURAZIONE ---")

    # Controlla cosa manca
    mancanti = []
    if "verde" not in dati_calibrazione_temporanei: mancanti.append("Verde")
    if "non_verde" not in dati_calibrazione_temporanei: mancanti.append("Rosso (non_verde)")
    if "buio" not in dati_calibrazione_temporanei: mancanti.append("Spento (buio)")
    if "machine_id" not in dati_calibrazione_temporanei: mancanti.append("ID Macchina")
    # Aggiunto controllo per integration_time
    if "integration_time" not in dati_calibrazione_temporanei: mancanti.append("Tempo Integrazione")

    # Stampa valori impostati
    print(f"  Verde:             {format_rgb(dati_calibrazione_temporanei.get('verde'))}")
    print(f"  Rosso (non_verde):   {format_rgb(dati_calibrazione_temporanei.get('non_verde'))}")
    print(f"  Spento (buio):     {format_rgb(dati_calibrazione_temporanei.get('buio'))}")
    print(f"  ID Macchina:       {dati_calibrazione_temporanei.get('machine_id', 'N/D')}")
    print(f"  Tempo Integrazione: {dati_calibrazione_temporanei.get('integration_time', 'N/D')}ms")
    print("-" * 36)

    if mancanti:
        print(f"⚠️ ATTENZIONE: I seguenti valori non sono impostati:")
        for item in mancanti:
            print(f"   - {item}")
        print("   Lo script di monitoraggio potrebbe non avviarsi correttamente.")
        conferma = input(f"Salvare comunque in '{FILE_CALIBRAZIONE}'? (s/n): ").lower()
    else:
        conferma = input(f"Tutti i valori sono impostati. Salvare? (s/n): ").lower()

    if conferma == 's':
        try:
            # Assicura che la directory config esista
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(FILE_CALIBRAZIONE, 'w') as f:
                json.dump(dati_calibrazione_temporanei, f, indent=4)
            print(f"\n✅ Dati salvati con successo in '{FILE_CALIBRAZIONE}'!")
            return True  # Salvataggio completato
        except Exception as e:
            print(f"\n❌ ERRORE durante il salvataggio del file: {e}")
            input("   Premi INVIO per tornare al menu...")
            return False
    else:
        print("   Salvataggio annullato.")
        return False


# --- Ciclo Principale ---

def main():
    # Carica i dati PRIMA di inizializzare il sensore
    carica_dati_esistenti()

    sensor_main = inizializza_sensore()
    if not sensor_main:
        sys.exit(1)

    print("\nIMPORTANTE: Posiziona il sensore in modo che 'veda' le luci.")

    while True:
        # Avvia la lettura live in background
        stop_live_thread.clear()
        live_thread = threading.Thread(target=debug_lettura_live_thread, daemon=True)
        live_thread.start()

        stampa_menu()

        # Aspetta 3 secondi per mostrare la lettura live
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            stop_live_thread.set();
            live_thread.join();
            print("\nUscita.");
            break

        # Ferma la lettura live per accettare l'input
        stop_live_thread.set()
        live_thread.join(timeout=1.0)  # Aspetta che il thread termini

        scelta = input("Inserisci la tua scelta (1-7): ")

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
            print(f"✅ 'Rosso (non_verde)' registrato: {valore}")
            time.sleep(1)

        elif scelta == '3':
            print("\n--- 3. Campiona SPENTO ---")
            print("Ora fai in modo che la macchina spenga la luce (stato BUIO).")
            input("Quando è pronta, premi INVIO per avviare il campionamento...")
            valore = leggi_rgb_stabilizzato(sensor_main)
            dati_calibrazione_temporanei["buio"] = valore
            print(f"✅ 'Spento (buio)' registrato: {valore}")
            time.sleep(1)

        elif scelta == '4':
            print("\n--- 4. Imposta ID Macchina (MQTT) ---")
            print(f"ID Attuale: {dati_calibrazione_temporanei.get('machine_id', 'Nessuno')}")
            nuovo_id = input("Inserisci il nuovo ID (es. macchina_02_TCS): ").strip()
            if nuovo_id:
                dati_calibrazione_temporanei["machine_id"] = nuovo_id
                print(f"✅ ID Macchina impostato: {nuovo_id}")
            else:
                print("   Nessuna modifica.")
            time.sleep(1)

        elif scelta == '5':
            print("\n--- 5. Imposta Tempo Integrazione (ms) ---")
            current_time = dati_calibrazione_temporanei.get('integration_time', 250)
            print(f"Valore Attuale: {current_time}ms")
            print("Valore raccomandato per stabilità: 250")
            print("Valori più bassi (es. 150) sono più veloci ma più sensibili al 'flicker'.")
            print("Valori validi: da 2.4 a 700 (circa).")

            try:
                nuovo_tempo_str = input(f"Inserisci nuovo valore (INVIO per annullare): ").strip()
                if nuovo_tempo_str:
                    nuovo_tempo = int(nuovo_tempo_str)
                    if 5 <= nuovo_tempo <= 700:
                        dati_calibrazione_temporanei["integration_time"] = nuovo_tempo
                        print(f"✅ Tempo impostato: {nuovo_tempo}ms.")
                        print("   Applico l'impostazione al sensore per questa sessione...")
                        sensor_main.integration_time = nuovo_tempo
                        print("✅ Fatto.")
                    else:
                        print("   Valore fuori range (deve essere tra 5 e 700).")
                else:
                    print("   Nessuna modifica.")
            except ValueError:
                print("   Input non valido. Inserisci solo un numero.")
            time.sleep(1)

        elif scelta == '6':
            if salva_file_calibrazione():
                break  # Esce dal loop while True

        elif scelta == '7':
            print("\nUscita senza salvataggio.")
            break  # Esce dal loop while True

        else:
            print("Scelta non valida. Inserisci un numero da 1 a 7.")
            time.sleep(1)

    print("Programma di calibrazione terminato.")


if __name__ == "__main__":
    main()