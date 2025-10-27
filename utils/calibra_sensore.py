#!/usr/bin/env python3
"""
SCRIPT DI CALIBRAZIONE MANUALE (Ambiente Reale) - Versione "Tutto in Uno"

Questo script esegue la calibrazione e MOSTRA LA LETTURA LIVE contemporaneamente
utilizzando il multithreading.

Carica i valori esistenti all'avvio per permettere calibrazioni parziali.
"""

import time
import json
import sys
import os
import threading  # Importato per la lettura live in background

try:
    import board
    import busio
    import adafruit_tcs34725
except ImportError:
    print("❌ Errore: Librerie del sensore non trovate.")
    print("   Esegui: pip install adafruit-blinka adafruit-circuitpython-tcs4725")
    sys.exit(1)

# --- Parametri ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
FILE_CALIBRAZIONE = os.path.join(CONFIG_DIR, "calibrazione.json")

CAMPIONI_PER_LETTURA = 10  # Numero di letture da mediare per un valore stabile

# Dizionario per tenere i valori
# Verrà popolato dalla funzione 'carica_calibrazione_esistente'
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


# --- NUOVA FUNZIONE: Caricamento Dati Esistenti ---
def carica_calibrazione_esistente():
    """Tenta di caricare i dati dal file JSON all'avvio."""
    global dati_calibrazione_temporanei
    try:
        if os.path.exists(FILE_CALIBRAZIONE):
            with open(FILE_CALIBRAZIONE, 'r') as f:
                dati = json.load(f)
                # Assicurati che le chiavi siano corrette e popola
                if "verde" in dati:
                    dati_calibrazione_temporanei["verde"] = dati["verde"]
                if "non_verde" in dati:
                    dati_calibrazione_temporanei["non_verde"] = dati["non_verde"]
                if "buio" in dati:
                    dati_calibrazione_temporanei["buio"] = dati["buio"]

            if dati_calibrazione_temporanei:
                print(f"✅ Caricati dati di calibrazione esistenti da '{FILE_CALIBRAZIONE}'")
            else:
                print(f"ℹ️ File di calibrazione trovato ma vuoto. Inizio da zero.")
        else:
            print(f"ℹ️ Nessun file di calibrazione esistente trovato. Inizio da zero.")
    except Exception as e:
        print(f"⚠️ Errore nel caricare '{FILE_CALIBRAZIONE}': {e}. Inizio da zero.")


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


# --- FUNZIONE PER THREAD LIVE ---

def _live_feed_worker(sensor, stop_event):
    """
    Funzione eseguita in un thread separato per mostrare
    la lettura live senza bloccare il menu principale.
    """
    print("Avvio lettura live in background...")
    try:
        while not stop_event.is_set():
            rgb = leggi_rgb_attuale(sensor)
            # Stampa sulla stessa riga (\r) per un output pulito
            # Aggiungiamo spazi alla fine per pulire la riga precedente
            print(f"   Lettura Live: R={rgb[0]:<3} G={rgb[1]:<3} B={rgb[2]:<3}   ", end="\r")
            # Controlla l'evento di stop ogni 0.1 secondi
            stop_event.wait(0.1)
    except Exception:
        pass  # Il thread termina se c'è un errore (es. sensore scollegato)
    finally:
        # Pulisce la riga prima di uscire
        print(" " * 50, end="\r")
        print("Lettura live fermata.")


# --- Funzioni Menu ---

def stampa_menu():
    """Mostra il menu delle opzioni e lo stato della calibrazione."""
    # os.system('clear') # Rimuovere se dà fastidio
    print("\n" + "=" * 50)
    print("--- MENU CALIBRAZIONE (con Lettura Live) ---")
    print("=" * 50)

    # Mostra cosa è già stato calibrato (caricato o appena fatto)
    stato_verde = "✅ CALIBRATO" if "verde" in dati_calibrazione_temporanei else "❌ DA FARE"
    stato_rosso = "✅ CALIBRATO" if "non_verde" in dati_calibrazione_temporanei else "❌ DA FARE"
    stato_spento = "✅ CALIBRATO" if "buio" in dati_calibrazione_temporanei else "❌ DA FARE"

    print(f"1. Campiona 'Verde' (luce fisso o lampeggiante)       {stato_verde}")
    print(f"2. Campiona 'Rosso' (luce fisso o lampeggiante)       {stato_rosso}")
    print(f"3. Campiona 'Spento' (fisso)                          {stato_spento}")
    print("---------------------------------------------")
    print("5. Salva calibrazione su file ed Esci")
    print("6. Esci SENZA salvare")
    print("=" * 50)


# --- FUNZIONE SALVATAGGIO MODIFICATA ---
def salva_file_calibrazione():
    """Controlla (con avviso) e salva i dati di calibrazione."""

    # Controlla quali chiavi mancano
    chiavi_mancanti = []
    if "verde" not in dati_calibrazione_temporanei:
        chiavi_mancanti.append("Verde")
    if "non_verde" not in dati_calibrazione_temporanei:
        chiavi_mancanti.append("Rosso")
    if "buio" not in dati_calibrazione_temporanei:
        chiavi_mancanti.append("Spento")

    print("\n--- RIEPILOGO CALIBRAZIONE ---")
    # Mostra i valori attuali (usa .get() per evitare errori se mancano)
    print(f"  Verde:           {dati_calibrazione_temporanei.get('verde', '--- NON IMPOSTATO ---')}")
    print(f"  Rosso (non_verde): {dati_calibrazione_temporanei.get('non_verde', '--- NON IMPOSTATO ---')}")
    print(f"  Spento (buio):     {dati_calibrazione_temporanei.get('buio', '--- NON IMPOSTATO ---')}")
    print("------------------------------")

    # Se mancano chiavi, stampa un AVVISO invece di un errore
    if chiavi_mancanti:
        print(f"\n⚠️ ATTENZIONE: Manca la calibrazione per: {', '.join(chiavi_mancanti)}.")
        conferma = input(f"Salvare comunque i dati (parziali) in '{FILE_CALIBRAZIONE}'? (s/n): ").lower()
    else:
        print("Tutti i valori sono impostati.")
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
            print(f"\n❌ ERRORE durante il salvataggio del file: {e}")
            input("   Premi INVIO per tornare al menu...")
            return False
    else:
        print("   Salvataggio annullato.")
        return False


# --- Ciclo Principale ---

def main():
    sensor = inizializza_sensore()
    if not sensor:
        sys.exit(1)  # Esce se il sensore non è trovato

    # --- MODIFICA CHIAVE ---
    # Carica i dati esistenti prima di iniziare il loop
    carica_calibrazione_esistente()

    print(f"\nIMPORTANTE: I dati verranno salvati in '{FILE_CALIBRAZIONE}'")
    print("Posiziona il sensore in modo che 'veda' le luci.")

    try:
        while True:
            # --- Blocco Lettura Live ---
            stop_live_feed = threading.Event()
            feed_thread = threading.Thread(
                target=_live_feed_worker,
                args=(sensor, stop_live_feed),
                daemon=True
            )
            feed_thread.start()

            stampa_menu()

            print("\nOsserva la lettura live per 3 secondi...")
            time.sleep(3)

            print("\nFermo la lettura live per l'operazione...")
            stop_live_feed.set()
            feed_thread.join()

            scelta = input("Inserisci la tua scelta (1-3, 5-6): ")

            # --- Blocco Gestione Scelta ---
            if scelta == '1':
                print("\n--- 1. Campiona Verde ---")
                print("Ora fai in modo che la macchina mostri la luce Verde (fissa o lampeggiante).")
                print("IMPORTANTE: campiona il momento in cui la luce è ACCESA.")
                input("Quando è pronta, premi INVIO per avviare il campionamento...")
                valore = leggi_rgb_stabilizzato(sensor)
                dati_calibrazione_temporanei["verde"] = valore
                print(f"✅ 'Verde' registrato: {valore}")
                time.sleep(1)

            elif scelta == '2':
                print("\n--- 2. Campiona Rosso ---")
                print("Ora fai in modo che la macchina mostri la luce Rossa (fissa o lampeggiante).")
                print("IMPORTANTE: campiona il momento in cui la luce è ACCESA.")
                input("Quando è pronta, premi INVIO per avviare il campionamento...")
                valore = leggi_rgb_stabilizzato(sensor)
                dati_calibrazione_temporanei["non_verde"] = valore
                print(f"✅ 'Rosso' (salvato come 'non_verde') registrato: {valore}")
                time.sleep(1)

            elif scelta == '3':
                print("\n--- 3. Campiona Spento ---")
                print("Ora fai in modo che la macchina spenga la luce (stato Spento fisso).")
                input("Quando è pronta, premi INVIO per avviare il campionamento...")
                valore = leggi_rgb_stabilizzato(sensor)
                dati_calibrazione_temporanei["buio"] = valore
                print(f"✅ 'Spento' (salvato come 'buio') registrato: {valore}")
                time.sleep(1)

            elif scelta == '5':
                if salva_file_calibrazione():
                    break  # Esce dal loop while True

            elif scelta == '6':
                print("\nUscita senza salvataggio.")
                break  # Esce dal loop while True

            else:
                print("Scelta non valida. Riprova.")
                time.sleep(1)

            # Alla fine del loop, il thread live verrà ricreato e ripartirà

    except KeyboardInterrupt:
        print("\n🛑 Uscita forzata.")
    finally:
        print("Programma di calibrazione terminato.")


if __name__ == "__main__":
    main()

