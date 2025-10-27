#!/usr/bin/env python3
"""
SCRIPT DI CALIBRAZIONE MANUALE (Ambiente Reale)

Questo script è pensato per calibrare il sensore in un ambiente reale
dove i LED sono controllati da macchine esterne (NON da questo Raspberry).

L'utente dice allo script cosa sta guardando (verde, rosso, buio)
e lo script campiona i valori RGB corrispondenti.

I dati vengono salvati in ../config/calibrazione.json
"""

import time
import json
import sys
import os

try:
    import board
    import busio
    import adafruit_tcs34725
except ImportError:
    print("❌ Errore: Librerie del sensore non trovate.")
    print("   Esegui: pip install adafruit-blinka adafruit-circuitpython-tcs34725")
    sys.exit(1)

# --- Parametri ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
FILE_CALIBRAZIONE = os.path.join(CONFIG_DIR, "calibrazione.json")

CAMPIONI_PER_LETTURA = 10  # Numero di letture da mediare per un valore stabile

# Dizionario per tenere i valori prima di salvarli
dati_calibrazione_temporanei = {}


# --- Inizializzazione Hardware ---

def inizializza_sensore():
    """Inizializza il sensore TCS34725."""
    print("🔧 Inizializzazione sensore TCS34725...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        sensor.integration_time = 150
        sensor.gain = 4
        print("✅ Sensore inizializzato.")
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
        if len(result) >= 3:
            return result[:3]  # R, G, B
    except Exception:
        pass  # Tenta con il metodo raw

    try:  # Fallback
        raw = sens.color_raw
        r = min(255, int(raw[0] / 256))
        g = min(255, int(raw[1] / 256))
        b = min(255, int(raw[2] / 256))
        return r, g, b
    except Exception:
        return 0, 0, 0  # Errore grave


def leggi_rgb_stabilizzato(sensor, campioni=CAMPIONI_PER_LETTURA):
    """
    Legge il sensore 'campioni' volte e restituisce i valori medi R, G, B.
    """
    tot_r, tot_g, tot_b = 0, 0, 0
    letture_valide = 0

    print(f"   Avvio campionamento ({campioni} letture)...")

    for i in range(campioni):
        r, g, b = 0, 0, 0
        try:
            r, g, b = leggi_rgb_attuale(sensor)
            letture_valide += 1
            tot_r += r
            tot_g += g
            tot_b += b
            print(f"   Lettura {i + 1}/{campioni}: R={r}, G={g}, B={b}   ", end="\r")
        except Exception:
            print(f"   Lettura {i + 1}/{campioni}: FALLITA")

        time.sleep(0.05)  # Piccola pausa

    print("\n   Campionamento terminato.")
    if letture_valide == 0:
        print("      ATTENZIONE: Nessuna lettura valida ottenuta.")
        return {"R": 0, "G": 0, "B": 0}

    # Calcola la media
    avg_r = int(tot_r / letture_valide)
    avg_g = int(tot_g / letture_valide)
    avg_b = int(tot_b / letture_valide)

    return {"R": avg_r, "G": avg_g, "B": avg_b}


# --- Funzioni Menu ---

def stampa_menu():
    """Mostra il menu delle opzioni e lo stato della calibrazione."""
    print("\n" + "=" * 50)
    print("--- MENU CALIBRAZIONE MANUALE AMBIENTE REALE ---")
    print("=" * 50)

    # Mostra cosa è già stato calibrato in questa sessione
    stato_verde = "✅ CALIBRATO" if "verde" in dati_calibrazione_temporanei else "❌ DA FARE"
    stato_non_verde = "✅ CALIBRATO" if "non_verde" in dati_calibrazione_temporanei else "❌ DA FARE"
    stato_buio = "✅ CALIBRATO" if "buio" in dati_calibrazione_temporanei else "❌ DA FARE"

    print(f"1. Campiona 'VERDE' (luce fissa)       {stato_verde}")
    print(f"2. Campiona 'NON-VERDE' (luce fissa)   {stato_non_verde}")
    print(f"3. Campiona 'BUIO' (luce spenta)       {stato_buio}")
    print("---------------------------------------------")
    print("4. Mostra lettura sensore in tempo reale (per debug)")
    print("5. Salva calibrazione su file ed Esci")
    print("6. Esci SENZA salvare")
    print("=" * 50)


def salva_file_calibrazione():
    """Controlla e salva i dati di calibrazione."""

    # Controlla se abbiamo tutti i dati
    if not all(k in dati_calibrazione_temporanei for k in ("verde", "non_verde", "buio")):
        print("\n❌ ERRORE: Impossibile salvare.")
        print("   Devi prima campionare TUTTI e 3 i valori (Verde, Non-Verde e Buio).")
        input("   Premi INVIO per tornare al menu...")
        return False

    # Mostra un riepilogo
    print("\n--- RIEPILOGO CALIBRAZIONE ---")
    print(f"  VERDE:     {dati_calibrazione_temporanei['verde']}")
    print(f"  NON-VERDE: {dati_calibrazione_temporanei['non_verde']}")
    print(f"  BUIO:      {dati_calibrazione_temporanei['buio']}")
    print("------------------------------")

    conferma = input(f"Salvare questi valori in '{FILE_CALIBRAZIONE}'? (s/n): ").lower()

    if conferma == 's':
        try:
            # Assicura che la directory esista
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(FILE_CALIBRAZIONE, 'w') as f:
                json.dump(dati_calibrazione_temporanei, f, indent=4)
            print(f"\n✅ Dati salvati con successo in '{FILE_CALIBRAZIONE}'!")
            return True  # Salvataggio completato
        except Exception as e:
            print(f"\n❌ ERRORE during saving the file: {e}")
            input("   Premi INVIO per tornare al menu...")
            return False
    else:
        print("   Salvataggio annullato.")
        return False


def debug_lettura_live(sensor):
    """Mostra i valori letti dal sensore in tempo reale."""
    print("\n--- LETTURA LIVE (DEBUG) ---")
    print("Posiziona il sensore e osserva i valori.")
    print("Premi CTRL+C per tornare al menu principale.")
    try:
        while True:
            rgb = leggi_rgb_attuale(sensor)
            # Stampa sulla stessa riga (\r) per un output pulito
            print(f"   Lettura: R={rgb[0]:<3} G={rgb[1]:<3} B={rgb[2]:<3}   ", end="\r")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nFine lettura live.")


# --- Ciclo Principale ---

def main():
    sensor = inizializza_sensore()
    if not sensor:
        sys.exit(1)  # Esce se il sensore non è trovato

    print(f"\nIMPORTANTE: I dati verranno salvati in '{FILE_CALIBRAZIONE}'")
    print("Posiziona il sensore in modo che 'veda' le luci")
    print("della macchina che vuoi calibrare.")

    while True:
        stampa_menu()
        scelta = input("Inserisci la tua scelta (1-6): ")

        if scelta == '1':
            print("\n--- 1. Campiona VERDE ---")
            print("Ora fai in modo che la macchina mostri la luce VERDE FISSA.")
            input("Quando è pronta, premi INVIO per avviare il campionamento...")
            valore = leggi_rgb_stabilizzato(sensor)
            dati_calibrazione_temporanei["verde"] = valore
            print(f"✅ 'Verde' registrato: {valore}")
            time.sleep(1)

        elif scelta == '2':
            print("\n--- 2. Campiona NON-VERDE ---")
            print("Ora fai in modo che la macchina mostri la luce NON-VERDE (Rossa/Gialla) FISSA.")
            input("Quando è pronta, premi INVIO per avviare il campionamento...")
            valore = leggi_rgb_stabilizzato(sensor)
            dati_calibrazione_temporanei["non_verde"] = valore
            print(f"✅ 'Non-Verde' registrato: {valore}")
            time.sleep(1)

        elif scelta == '3':
            print("\n--- 3. Campiona BUIO ---")
            print("Ora fai in modo che la macchina spenga la luce (stato BUIO).")
            input("Quando è pronta, premi INVIO per avviare il campionamento...")
            valore = leggi_rgb_stabilizzato(sensor)
            dati_calibrazione_temporanei["buio"] = valore
            print(f"✅ 'Buio' registrato: {valore}")
            time.sleep(1)

        elif scelta == '4':
            debug_lettura_live(sensor)

        elif scelta == '5':
            # La funzione 'salva_file_calibrazione' gestisce tutto
            # e ritorna True se il salvataggio è riuscito e dobbiamo uscire.
            if salva_file_calibrazione():
                break  # Esce dal loop while True

        elif scelta == '6':
            print("\nUscita senza salvataggio.")
            break  # Esce dal loop while True

        else:
            print("Scelta non valida. Inserisci un numero da 1 a 6.")
            time.sleep(1)

    print("Programma di calibrazione terminato.")


if __name__ == "__main__":
    main()
