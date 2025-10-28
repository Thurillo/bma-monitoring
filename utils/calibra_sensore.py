#!/usr/bin/env python3
"""
SCRIPT: CALIBRAZIONE MANUALE (Ambiente Reale) + IMPOSTAZIONE ID

Questo script permette di calibrare i colori E di impostare l'ID Macchina
per la connessione MQTT.

Salva tutto in 'config/calibrazione.json'.
Carica i valori esistenti all'avvio.
Mostra una lettura live in background.
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
CALIBRATION_FILE = os.path.join(CONFIG_DIR, "calibrazione.json")

CAMPIONI_PER_LETTURA = 10  # Numero di letture da mediare per un valore stabile
LIVE_READING_INTERVAL = 0.5  # Intervallo (secondi) per la lettura live

# Dizionario per tenere i valori prima di salvarli
dati_calibrazione_temporanei = {}


# --- Threading per Lettura Live ---
class LiveReadingThread(threading.Thread):
    def __init__(self, sensor):
        super().__init__()
        self.sensor = sensor
        self._stop_event = threading.Event()
        self.daemon = True  # Il thread muore se lo script principale esce

    def run(self):
        """Ciclo di vita del thread."""
        while not self._stop_event.is_set():
            try:
                rgb = leggi_rgb_attuale(self.sensor)
                # Stampa e sovrascrive la riga
                print(f"   Lettura Live: R={rgb[0]:<3} G={rgb[1]:<3} B={rgb[2]:<3}   ", end="\r")
            except Exception:
                print("   Lettura Live: ERRORE SENSOR    ", end="\r")

            # Attendi l'intervallo o lo stop
            self._stop_event.wait(LIVE_READING_INTERVAL)

        # Pulisci la riga quando il thread si ferma
        print(" " * 50, end="\r")

    def stop(self):
        """Richiede al thread di fermarsi."""
        self._stop_event.set()


# --- Inizializzazione Hardware ---

def inizializza_sensore():
    """Inizializza il sensore TCS34725."""
    print("🔧 Inizializzazione sensore TCS34725...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        # Usa le stesse impostazioni del monitor!
        sensor.integration_time = 250
        sensor.gain = 16
        print("✅ Sensore inizializzato (con gain/time aumentati).")
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
    tot_r, tot_g, tot_b = 0, 0, 0
    letture_valide = 0

    print(f"   Avvio campionamento ({campioni} letture)...")

    for i in range(campioni):
        r, g, b = 0, 0, 0
        try:
            r, g, b = leggi_rgb_attuale(sensor)
            letture_valide += 1;
            tot_r += r;
            tot_g += g;
            tot_b += b
            print(f"   Lettura {i + 1}/{campioni}: R={r:<3} G={g:<3} B={b:<3}")
        except Exception:
            print(f"   Lettura {i + 1}/{campioni}: FALLITA")

        time.sleep(0.05)

    if letture_valide == 0:
        print("      ATTENZIONE: Nessuna lettura valida ottenuta.")
        return {"R": 0, "G": 0, "B": 0}

    avg_r = int(tot_r / letture_valide)
    avg_g = int(tot_g / letture_valide)
    avg_b = int(tot_b / letture_valide)

    return {"R": avg_r, "G": avg_g, "B": avg_b}


# --- Gestione File ---

def carica_dati_calibrazione():
    """Carica i dati esistenti da calibrazione.json, se esiste."""
    global dati_calibrazione_temporanei
    os.makedirs(CONFIG_DIR, exist_ok=True)  # Assicura che la dir esista

    if os.path.exists(CALIBRATION_FILE):
        try:
            with open(CALIBRATION_FILE, 'r') as f:
                dati_calibrazione_temporanei = json.load(f)
            print(f"✅ Dati di calibrazione precedenti caricati.")
        except Exception as e:
            print(f"⚠️ Errore nel caricare '{CALIBRATION_FILE}': {e}. Inizio con dati vuoti.")
            dati_calibrazione_temporanei = {}
    else:
        print("ℹ️ Nessun file di calibrazione esistente. Inizio da zero.")
        dati_calibrazione_temporanei = {}


def salva_dati_calibrazione():
    """Mostra un riepilogo e salva i dati su file."""

    print("\n--- RIEPILOGO CALIBRAZIONE ---")

    # Controlla ID Macchina
    if "machine_id" in dati_calibrazione_temporanei and dati_calibrazione_temporanei["machine_id"]:
        print(f"  ID Macchina: {dati_calibrazione_temporanei['machine_id']}")
    else:
        print("  ID Macchina: ❌ NON IMPOSTATO (Critico!)")

    # Controlla Colori
    for nome, chiave in [("Verde", "verde"), ("Rosso", "non_verde"), ("Spento", "buio")]:
        if chiave in dati_calibrazione_temporanei:
            print(f"  {nome:<11}: {dati_calibrazione_temporanei[chiave]}")
        else:
            print(f"  {nome:<11}: ❌ NON IMPOSTATO (Critico!)")

    print("------------------------------")

    # Avviso se manca qualcosa
    if not all(k in dati_calibrazione_temporanei for k in ("verde", "non_verde", "buio", "machine_id")):
        print("⚠️ ATTENZIONE: Mancano una o più configurazioni critiche.")
        print("   Il monitoraggio non funzionerà senza tutti i valori (ID e 3 colori).")

    conferma = input(f"Salvare questi valori in '{CALIBRATION_FILE}'? (s/n): ").lower()

    if conferma == 's':
        try:
            with open(CALIBRATION_FILE, 'w') as f:
                json.dump(dati_calibrazione_temporanei, f, indent=4)
            print(f"\n✅ Dati salvati con successo!")
            return True  # Salvataggio completato
        except Exception as e:
            print(f"\n❌ ERRORE durante il salvataggio: {e}")
            input("   Premi INVIO per tornare al menu...")
            return False
    else:
        print("   Salvataggio annullato.")
        return False


# --- Funzioni Menu ---

def format_rgb_string(data, key):
    """Helper per formattare i valori RGB nel menu."""
    if key in data:
        rgb = data[key]
        return f"R:{rgb['R']} G:{rgb['G']} B:{rgb['B']}"
    return "???"


def stampa_menu():
    """Mostra il menu delle opzioni e lo stato della calibrazione."""
    print("\n" + "=" * 50)
    print("--- MENU CALIBRAZIONE SENSORE E ID MACCHINA ---")
    print("=" * 50)

    # Mostra cosa è già stato calibrato
    stato_verde = "✅ CALIBRATO" if "verde" in dati_calibrazione_temporanei else "❌ DA FARE"
    stato_rosso = "✅ CALIBRATO" if "non_verde" in dati_calibrazione_temporanei else "❌ DA FARE"
    stato_spento = "✅ CALIBRATO" if "buio" in dati_calibrazione_temporanei else "❌ DA FARE"
    stato_id = "✅ IMPOSTATO" if "machine_id" in dati_calibrazione_temporanei else "❌ DA FARE"

    # Mostra valori attuali
    val_verde = format_rgb_string(dati_calibrazione_temporanei, 'verde')
    val_rosso = format_rgb_string(dati_calibrazione_temporanei, 'non_verde')
    val_spento = format_rgb_string(dati_calibrazione_temporanei, 'buio')
    val_id = dati_calibrazione_temporanei.get('machine_id', 'N/D')

    print(f"1. Campiona 'Verde'             {stato_verde:<12} (Attuale: {val_verde})")
    print(f"2. Campiona 'Rosso' (non_verde) {stato_rosso:<12} (Attuale: {val_rosso})")
    print(f"3. Campiona 'Spento' (buio)     {stato_spento:<12} (Attuale: {val_spento})")
    print(f"4. Imposta ID Macchina (MQTT)   {stato_id:<12} (Attuale: {val_id})")
    print("---------------------------------------------")
    print("5. Salva calibrazione su file ed Esci")
    print("6. Esci SENZA salvare")
    print("=" * 50)


# --- Ciclo Principale ---

def main():
    sensor = inizializza_sensore()
    if not sensor:
        sys.exit(1)

    # Carica i dati esistenti all'avvio
    carica_dati_calibrazione()

    print("\nIMPORTANTE: Posiziona il sensore in modo che 'veda' le luci.")

    live_reading_thread = None

    try:
        while True:
            # (Ri)Avvia il thread di lettura live
            if live_reading_thread is None or not live_reading_thread.is_alive():
                live_reading_thread = LiveReadingThread(sensor)
                live_reading_thread.start()

            stampa_menu()

            # Pausa per permettere all'utente di leggere il menu e la live
            time.sleep(3)

            # Ferma la lettura live per non sporcare l'input
            live_reading_thread.stop()
            live_reading_thread.join()  # Attendi che il thread termini
            print("Lettura live fermata.                ", end="\r")

            scelta = input(f"Inserisci la tua scelta (1-6): ")

            if scelta == '1':
                print("\n--- 1. Campiona 'Verde' (luce fisso o lampeggiante) ---")
                input("Quando la luce VERDE è attiva, premi INVIO...")
                valore = leggi_rgb_stabilizzato(sensor)
                dati_calibrazione_temporanei["verde"] = valore
                print(f"✅ 'Verde' registrato: {valore}")
                time.sleep(1)

            elif scelta == '2':
                print("\n--- 2. Campiona 'Rosso' (luce fisso o lampeggiante) ---")
                input("Quando la luce ROSSA è attiva, premi INVIO...")
                valore = leggi_rgb_stabilizzato(sensor)
                dati_calibrazione_temporanei["non_verde"] = valore
                print(f"✅ 'Rosso' (non_verde) registrato: {valore}")
                time.sleep(1)

            elif scelta == '3':
                print("\n--- 3. Campiona 'Spento' (fisso) ---")
                input("Quando la luce è SPENTA (buio), premi INVIO...")
                valore = leggi_rgb_stabilizzato(sensor)
                dati_calibrazione_temporanei["buio"] = valore
                print(f"✅ 'Spento' (buio) registrato: {valore}")
                time.sleep(1)

            elif scelta == '4':
                print("\n--- 4. Imposta ID Macchina (MQTT) ---")
                current_id = dati_calibrazione_temporanei.get("machine_id", "Nessuno")
                print(f"ID Attuale: {current_id}")
                new_id = input("Inserisci il nuovo ID Macchina (es: linea_01): ").strip()
                if new_id:
                    dati_calibrazione_temporanei["machine_id"] = new_id
                    print(f"✅ ID Macchina impostato su: {new_id}")
                else:
                    print("Annullato, ID non modificato.")
                time.sleep(1)

            elif scelta == '5':
                if salva_dati_calibrazione():
                    break  # Esce dal loop while True

            elif scelta == '6':
                print("\nUscita senza salvataggio.")
                break  # Esce dal loop while True

            else:
                print("Scelta non valida. Inserisci un numero da 1 a 6.")
                time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 Calibrazione interrotta dall'utente.")
    finally:
        if live_reading_thread and live_reading_thread.is_alive():
            live_reading_thread.stop()
            live_reading_thread.join()
        print("Programma di calibrazione terminato.")


if __name__ == "__main__":
    main()

