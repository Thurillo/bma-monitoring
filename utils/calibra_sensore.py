#!/usr/bin/env python3
# ---
# File: calibra_sensore.py
# Directory: utils/
# Ultima Modifica: 2026-01-11
# Versione: 1.22
# ---

"""
SCRIPT: CALIBRAZIONE MANUALE (Ambiente Reale)

V 1.22:
- Aggiornati i suggerimenti a video per riflettere le nuove raccomandazioni
  di stabilità:
  -> Buffer Size: Suggerito 100 (Alta stabilità, ritardo ~10s).
- Opzione 10 (Test) resta operativa in modalità ibrida (calibrato/raw).
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
    if not isinstance(rgb2_dict, dict) or not all(k in rgb2_dict for k in ('R', 'G', 'B')):
        return 9999.9
    r2, g2, b2 = rgb2_dict['R'], rgb2_dict['G'], rgb2_dict['B']
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


# --- Inizializzazione Hardware ---
def inizializza_sensore():
    print("🔧 Inizializzazione sensore TCS34725...")
    integration_time = dati_calibrazione_temporanei.get('integration_time', 150)
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
    return {"R": int(tot_r / letture_valide), "G": int(tot_g / letture_valide), "B": int(tot_b / letture_valide)}


def leggi_rgb_picco(sensor, valore_spento_dict, colore_target):
    picco_rgb_tuple = (0, 0, 0)
    picco_valore_canale = -1

    integration_time_sec = sensor.integration_time / 1000.0
    pausa_ciclo = max(0.05, integration_time_sec + 0.01)

    numero_campioni_totali = int(DURATA_CAMPIONAMENTO_PICCO_SEC / pausa_ciclo)

    print(f"   Avvio campionamento PICCO ({DURATA_CAMPIONAMENTO_PICCO_SEC} sec, ~{numero_campioni_totali} letture)...")
    print(f"   Cerco il valore {colore_target} più alto...")

    for i in range(numero_campioni_totali):
        lettura_tuple = leggi_rgb_attuale(sensor)
        distanza = calcola_distanza_rgb_raw(lettura_tuple, valore_spento_dict)
        print(
            f"   Campionamento {i + 1}/{numero_campioni_totali}: R={lettura_tuple[0]:<3} G={lettura_tuple[1]:<3} B={lettura_tuple[2]:<3} (Dist: {distanza:<5.1f})",
            end="\r")

        if distanza > DISTANZA_MINIMA_DA_SPENTO:
            if colore_target == "VERDE":
                valore_canale_corrente = lettura_tuple[1]  # Canale G
            else:
                valore_canale_corrente = lettura_tuple[0]  # Canale R

            if valore_canale_corrente > picco_valore_canale:
                picco_valore_canale = valore_canale_corrente
                picco_rgb_tuple = lettura_tuple
        time.sleep(pausa_ciclo)

    print("\n   ...Campionamento PICCO completato.")
    if picco_valore_canale == -1:
        print("   ⚠️ ATTENZIONE: Nessuna lettura valida trovata.")
    return {"R": picco_rgb_tuple[0], "G": picco_rgb_tuple[1], "B": picco_rgb_tuple[2]}


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


def test_sensore_continuo(sensor):
    print("\n" + "=" * 50)
    print("   TEST SENSORE - LETTURA CONTINUA")
    print("   Premi CTRL+C per fermare e tornare al menu.")
    print("=" * 50)

    has_calibration = all(k in dati_calibrazione_temporanei for k in ("verde", "non_verde", "buio"))
    if not has_calibration:
        print("⚠️  AVVISO: Calibrazione incompleta (Colori mancanti).")
        print("   Il test mostrerà SOLO i valori RGB raw (niente rilevamento stato).")
        time.sleep(2)
        target_verde = target_rosso = target_buio = None
    else:
        target_verde = dati_calibrazione_temporanei['verde']
        target_rosso = dati_calibrazione_temporanei['non_verde']
        target_buio = dati_calibrazione_temporanei['buio']

    if has_calibration:
        print(f"{'RGB Letto':<15} | {'Dist. VERDE':<12} | {'Dist. ROSSO':<12} | {'Dist. BUIO':<12} | {'STATO':<10}")
    else:
        print(f"{'RGB Letto':<15} | {'STATO':<10}")
    print("-" * 75)

    try:
        while True:
            try:
                rgb = leggi_rgb_attuale(sensor)
                rgb_str = f"{rgb[0]},{rgb[1]},{rgb[2]}"

                if has_calibration:
                    dist_v = calcola_distanza_rgb_raw(rgb, target_verde)
                    dist_r = calcola_distanza_rgb_raw(rgb, target_rosso)
                    dist_b = calcola_distanza_rgb_raw(rgb, target_buio)
                    distanze = {"VERDE": dist_v, "ROSSO": dist_r, "SPENTO": dist_b}
                    stato = min(distanze, key=distanze.get)
                    print(f"{rgb_str:<15} | {dist_v:<12.1f} | {dist_r:<12.1f} | {dist_b:<12.1f} | {stato:<10}",
                          end="\r")
                else:
                    print(f"{rgb_str:<15} | {'NON CALIB.':<10}", end="\r")
                time.sleep(0.2)
            except Exception as e:
                print(f"\n❌ ERRORE NEL LOOP DI TEST: {e} - Riprovo...", end="\r")
                time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Test interrotto dall'utente.")
        time.sleep(1)


# --- Funzioni Menu ---
def carica_dati_esistenti():
    global dati_calibrazione_temporanei
    try:
        if os.path.exists(FILE_CALIBRAZIONE):
            with open(FILE_CALIBRAZIONE, 'r') as f:
                dati_calibrazione_temporanei = json.load(f)
            print(f"✅ Dati caricati da '{FILE_CALIBRAZIONE}'")
        else:
            print("ℹ️ Nessun file trovato. Si parte da zero.")
    except Exception as e:
        print(f"⚠️ Errore caricamento: {e}. Si parte da zero.")
        dati_calibrazione_temporanei = {}


def format_rgb(valore):
    if not isinstance(valore, dict) or not all(k in valore for k in ('R', 'G', 'B')):
        return "N/D"
    return f"R:{valore['R']} G:{valore['G']} B:{valore['B']}"


def stampa_menu():
    print("\n" + "=" * 55)
    print("--- MENU CALIBRAZIONE SENSORE E CONFIGURAZIONE ---")
    print("=" * 55)

    v = dati_calibrazione_temporanei.get('verde')
    stato_verde = f"✅ CALIBRATO ({format_rgb(v)})" if v else "❌ DA FARE"

    r = dati_calibrazione_temporanei.get('non_verde')
    stato_rosso = f"✅ CALIBRATO ({format_rgb(r)})" if r else "❌ DA FARE"

    b = dati_calibrazione_temporanei.get('buio')
    stato_buio = f"✅ CALIBRATO ({format_rgb(b)})" if b else "❌ DA FARE"

    machine_id = dati_calibrazione_temporanei.get('machine_id')
    stato_id = f"✅ IMPOSTATO ({machine_id})" if machine_id else "❌ DA IMPOSTARE"

    integration_time = dati_calibrazione_temporanei.get('integration_time', 150)
    stato_integrazione = f"✅ IMPOSTATO ({integration_time}ms)"

    gain = dati_calibrazione_temporanei.get('gain', 4)
    stato_gain = f"✅ IMPOSTATO ({gain}x)"

    debug_logging = dati_calibrazione_temporanei.get('debug_logging', False)
    stato_debug = "✅ ABILITATO" if debug_logging else "❌ DISABILITATO"

    buffer_size = dati_calibrazione_temporanei.get('buffer_size', 100)
    stato_buffer = f"✅ IMPOSTATO ({buffer_size} letture)"

    soglia = dati_calibrazione_temporanei.get('steady_state_threshold', 90)
    stato_soglia = f"✅ IMPOSTATO ({soglia}%)"

    print(f"1. Campiona 'Verde' (PICCO luce)                 {stato_verde}")
    print(f"2. Campiona 'Rosso' (PICCO luce)                 {stato_rosso}")
    print(f"3. Campiona 'Spento' (MEDIA buio)                {stato_buio}")
    print("-" * 55)
    print(f"4. Imposta ID Macchina (per MQTT)                  {stato_id}")
    print(f"5. Imposta Tempo Integrazione (Sensore)          {stato_integrazione}")
    print(f"6. Imposta Gain Sensore (Sensibilità)            {stato_gain}")
    print(f"7. Abilita/Disabilita Log di Debug                 {stato_debug}")
    print(f"8. Imposta Buffer Size (Stabilità Monitor)       {stato_buffer}")
    print(f"9. Imposta Soglia Stabilità (es. 90%)            {stato_soglia}")
    print("-" * 55)
    print(f"10. TEST SENSORE (Lettura Continua)              🔍")
    print("-" * 55)
    print("11. Salva calibrazione e configurazione su file ed Esci")
    print("12. Esci SENZA salvare")
    print("=" * 55)


def salva_file_calibrazione():
    print("\n--- RIEPILOGO CONFIGURAZIONE ---")

    if "integration_time" not in dati_calibrazione_temporanei:
        dati_calibrazione_temporanei["integration_time"] = 150
    if "gain" not in dati_calibrazione_temporanei:
        dati_calibrazione_temporanei["gain"] = 4
    if "debug_logging" not in dati_calibrazione_temporanei:
        dati_calibrazione_temporanei["debug_logging"] = False
    if "buffer_size" not in dati_calibrazione_temporanei:
        dati_calibrazione_temporanei["buffer_size"] = 100  # Nuovo default suggerito
    if "steady_state_threshold" not in dati_calibrazione_temporanei:
        dati_calibrazione_temporanei["steady_state_threshold"] = 90

    mancanti = []
    if "verde" not in dati_calibrazione_temporanei: mancanti.append("Verde")
    if "non_verde" not in dati_calibrazione_temporanei: mancanti.append("Rosso")
    if "buio" not in dati_calibrazione_temporanei: mancanti.append("Spento")
    if "machine_id" not in dati_calibrazione_temporanei: mancanti.append("ID Macchina")

    print(f"  Verde:             {format_rgb(dati_calibrazione_temporanei.get('verde'))}")
    print(f"  Rosso:             {format_rgb(dati_calibrazione_temporanei.get('non_verde'))}")
    print(f"  Spento:            {format_rgb(dati_calibrazione_temporanei.get('buio'))}")
    print(f"  ID Macchina:       {dati_calibrazione_temporanei.get('machine_id', 'N/D')}")
    print(f"  Tempo Integrazione: {dati_calibrazione_temporanei.get('integration_time', 'N/D')}ms")
    print(f"  Gain Sensore:      {dati_calibrazione_temporanei.get('gain', 'N/D')}x")
    print(
        f"  Log di Debug:      {'Abilitato' if dati_calibrazione_temporanei.get('debug_logging') else 'Disabilitato'}")
    print(f"  Buffer Size:       {dati_calibrazione_temporanei.get('buffer_size', 'N/D')} letture")
    print(f"  Soglia Stabilità:  {dati_calibrazione_temporanei.get('steady_state_threshold', 'N/D')}%")
    print("-" * 36)

    conferma = 'n'
    if mancanti:
        print(f"⚠️ ATTENZIONE: Valori mancanti:")
        for m in mancanti: print(f"   - {m}")
        conferma = input(f"Salvare comunque? (s/n): ").lower()
    else:
        conferma = input(f"Salvare? (s/n): ").lower()

    if conferma == 's':
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(FILE_CALIBRAZIONE, 'w') as f:
                json.dump(dati_calibrazione_temporanei, f, indent=4)
            print(f"\n✅ Dati salvati in '{FILE_CALIBRAZIONE}'!")
            return True
        except Exception as e:
            print(f"\n❌ ERRORE salvataggio: {e}")
            return False
    else:
        print("   Salvataggio annullato.")
        return False


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

        scelta = input("Inserisci la tua scelta (1-12): ")

        if scelta == '1':
            if 'buio' not in dati_calibrazione_temporanei:
                print("\n❌ Calibra prima 'Spento' (Opzione 3).")
                time.sleep(2);
                continue
            print("\n--- 1. Campiona PICCO VERDE ---")
            input(f"Mostra luce VERDE e premi INVIO...")
            val = leggi_rgb_picco(sensor_main, dati_calibrazione_temporanei['buio'], "VERDE")
            dati_calibrazione_temporanei["verde"] = val
            print(f"✅ 'Verde' registrato: {val}")

        elif scelta == '2':
            if 'buio' not in dati_calibrazione_temporanei:
                print("\n❌ Calibra prima 'Spento' (Opzione 3).")
                time.sleep(2);
                continue
            print("\n--- 2. Campiona PICCO ROSSO ---")
            input(f"Mostra luce ROSSA e premi INVIO...")
            val = leggi_rgb_picco(sensor_main, dati_calibrazione_temporanei['buio'], "ROSSO")
            dati_calibrazione_temporanei["non_verde"] = val
            print(f"✅ 'Rosso' registrato: {val}")

        elif scelta == '3':
            print("\n--- 3. Campiona MEDIA SPENTO ---")
            print("Copri il sensore (BUIO).")
            input("Premi INVIO...")
            val = leggi_rgb_media(sensor_main, campioni=CAMPIONI_PER_MEDIA)
            dati_calibrazione_temporanei["buio"] = val
            print(f"✅ 'Spento' registrato: {val}")

        elif scelta == '4':
            print("\n--- 4. Imposta ID Macchina ---")
            curr = dati_calibrazione_temporanei.get('machine_id', 'N/D')
            nid = input(f"   Nuovo ID (Invio per '{curr}'): ")
            if nid: dati_calibrazione_temporanei["machine_id"] = nid

        elif scelta == '5':
            print("\n--- 5. Tempo Integrazione ---")
            curr = dati_calibrazione_temporanei.get('integration_time', 150)
            print(f"   Attuale: {curr}ms. CONSIGLIO: 150 (Veloce).")
            n = input(f"   Nuovo (Invio per {curr}): ")
            if n:
                try:
                    dati_calibrazione_temporanei["integration_time"] = int(n)
                    sensor_main = inizializza_sensore()
                except:
                    print("❌ Errore numero.")

        elif scelta == '6':
            print("\n--- 6. Gain Sensore ---")
            curr = dati_calibrazione_temporanei.get('gain', 4)
            print(f"   Attuale: {curr}x. CONSIGLIO: 4 (Low noise).")
            n = input(f"   Nuovo (Invio per {curr}): ")
            if n:
                try:
                    g = int(n)
                    if g in VALID_GAINS:
                        dati_calibrazione_temporanei["gain"] = g
                        sensor_main = inizializza_sensore()
                    else:
                        print(f"❌ Validi: {VALID_GAINS}")
                except:
                    print("❌ Errore numero.")

        elif scelta == '7':
            print("\n--- 7. Debug Logging ---")
            curr = dati_calibrazione_temporanei.get('debug_logging', False)
            dati_calibrazione_temporanei["debug_logging"] = not curr
            print(f"   Stato cambiato a: {not curr}")

        elif scelta == '8':
            print("\n--- 8. Imposta Buffer Size ---")
            curr = dati_calibrazione_temporanei.get('buffer_size', 100)
            print(f"   Attuale: {curr} letture.")
            # --- MODIFICA V 1.22: Testo suggerimento aggiornato ---
            print(f"   CONSIGLIO: 100 (Alta Stabilità, ~10s ritardo), 35 (Vecchio default).")
            # --- FINE MODIFICA ---
            n = input(f"   Nuovo (Invio per {curr}): ")
            if n:
                try:
                    v = int(n)
                    if 10 <= v <= 200:
                        dati_calibrazione_temporanei["buffer_size"] = v
                    else:
                        print("❌ Range 10-200.")
                except:
                    print("❌ Errore numero.")

        elif scelta == '9':
            print("\n--- 9. Soglia Stabilità ---")
            curr = dati_calibrazione_temporanei.get('steady_state_threshold', 90)
            print(f"   Attuale: {curr}%. CONSIGLIO: 90.")
            n = input(f"   Nuovo (Invio per {curr}): ")
            if n:
                try:
                    v = int(n)
                    if 80 <= v <= 98:
                        dati_calibrazione_temporanei["steady_state_threshold"] = v
                    else:
                        print("❌ Range 80-98.")
                except:
                    print("❌ Errore numero.")

        elif scelta == '10':
            test_sensore_continuo(sensor_main)

        elif scelta == '11':
            if salva_file_calibrazione(): break

        elif scelta == '12':
            print("\nUscita senza salvataggio.")
            break
        else:
            print("❌ Scelta non valida.")
            time.sleep(1)

    print("Programma terminato.")


if __name__ == "__main__":
    main()