#!/usr/bin/env python3
"""
Monitoraggio Semaforo BMA - Versione con Sensore TCS34725

Questo script sostituisce la logica OpenCV (webcam) con un sensore
di colore TCS34725 per rilevare lo stato del semaforo.

Logica:
1. Carica i valori RGB di riferimento (verde, non_verde, buio)
   dal file di calibrazione.
2. Legge in loop i valori RGB stabilizzati dal sensore.
3. Calcola quale dei 3 stati di riferimento è il più "vicino"
   (distanza euclidea) al valore letto.
4. Applica la logica di persistenza (3 secondi) identica alla versione
   con webcam.
5. Pubblica lo stato ("ROSSO", "VERDE", "SPENTO") sul topic MQTT.
"""

import paho.mqtt.client as mqtt
import json
import time
import os
import math
import sys

# Import per il sensore TCS34725
try:
    import board
    import busio
    import adafruit_tcs34725
except ImportError:
    print("❌ Errore: Librerie del sensore non trovate.")
    print("   Esegui: pip install adafruit-blinka adafruit-circuitpython-tcs34725")
    sys.exit(1)

# --- CONFIGURAZIONE LOGICA DI RILEVAMENTO ---
# Manteniamo la stessa logica di persistenza della versione precedente
STATE_PERSISTENCE_SECONDS = 3.0
CAMPIONI_PER_LETTURA = 10  # Numero di letture da mediare per un valore stabile

# --- CONFIGURAZIONE MQTT (e Percorsi) ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
MACHINE_ID = "macchina_01"
MQTT_TOPIC_STATUS = f"bma/{MACHINE_ID}/semaforo/stato"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
# Il nuovo file di configurazione
CALIBRAZIONE_FILE = os.path.join(CONFIG_DIR, "calibrazione.json")


# --- Funzioni di supporto MQTT ---

def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        print("✅ Connesso al broker MQTT!")
    else:
        print(f"❌ Connessione MQTT fallita: {rc}.")


def on_disconnect(client, userdata, flags, reason_code, properties):
    print(f"⚠️ Disconnesso da MQTT: {reason_code}.")


def load_config(file_path, config_name):
    if not os.path.exists(file_path):
        print(f"❌ Errore: File di configurazione '{config_name}' non trovato.")
        print(f"   Percorso cercato: {file_path}")
        print("   Esegui prima lo script 'utils/calibra_sensore.py'!")
        return None
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Errore nel caricamento di {file_path}: {e}")
        return None


# --- Funzioni Hardware Sensore ---

def inizializza_sensore():
    """Inizializza il sensore TCS34725."""
    print("🔧 Inizializzazione sensore TCS34725...")
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        sensor = adafruit_tcs34725.TCS34725(i2c)
        # Configura parametri (puoi affinarli se necessario)
        sensor.integration_time = 150
        sensor.gain = 4
        print("✅ Sensore inizializzato.")
        return sensor
    except Exception as e:
        print(f"❌ ERRORE: Impossibile trovare il sensore TCS34725.")
        print(f"   Controlla i collegamenti I2C.")
        print(f"   Dettagli: {e}")
        return None


def leggi_rgb_attuale(sens):
    """Esegue una singola lettura RGB, con fallback (presa da calibra_ambiente_reale.py)."""
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


def get_stabilized_rgb(sensor, campioni=CAMPIONI_PER_LETTURA):
    """
    Legge il sensore 'campioni' volte e restituisce i valori medi R, G, B.
    """
    tot_r, tot_g, tot_b = 0, 0, 0
    letture_valide = 0

    for _ in range(campioni):
        r, g, b = 0, 0, 0
        try:
            r, g, b = leggi_rgb_attuale(sensor)
            letture_valide += 1
            tot_r += r
            tot_g += g
            tot_b += b
        except Exception:
            pass  # Ignora lettura fallita

        time.sleep(0.01)  # Pausa minima tra letture

    if letture_valide == 0:
        return {"R": 0, "G": 0, "B": 0}  # Ritorna buio in caso di errore

    # Calcola la media
    avg_r = int(tot_r / letture_valide)
    avg_g = int(tot_g / letture_valide)
    avg_b = int(tot_b / letture_valide)

    return {"R": avg_r, "G": avg_g, "B": avg_b}


# --- NUOVA LOGICA DI RILEVAMENTO ---

def calcola_distanza_rgb(rgb1, rgb2):
    """
    Calcola la distanza Euclidea tra due colori RGB.
    rgb1 e rgb2 sono dizionari {"R": val, "G": val, "B": val}
    """
    dr = rgb1['R'] - rgb2['R']
    dg = rgb1['G'] - rgb2['G']
    db = rgb1['B'] - rgb2['B']
    return math.sqrt(dr * dr + dg * dg + db * db)


def get_sensor_status(current_rgb, calibrated_data):
    """
    Identifica lo stato confrontando il valore RGB attuale
    con i valori calibrati.
    """

    # Valori di riferimento dal file JSON
    ref_verde = calibrated_data['verde']
    ref_non_verde = calibrated_data['non_verde']
    ref_buio = calibrated_data['buio']

    # Calcola le distanze
    dist_verde = calcola_distanza_rgb(current_rgb, ref_verde)
    dist_non_verde = calcola_distanza_rgb(current_rgb, ref_non_verde)
    dist_buio = calcola_distanza_rgb(current_rgb, ref_buio)

    # Trova la distanza minima
    distanze = {
        "VERDE": dist_verde,
        "ROSSO": dist_non_verde,  # Mappiamo 'non_verde' a 'ROSSO'
        "SPENTO": dist_buio  # Mappiamo 'buio' a 'SPENTO'
    }

    # Restituisce il nome dello stato con la distanza minore
    stato_piu_vicino = min(distanze, key=distanze.get)

    # print(f"Debug: R={current_rgb['R']} G={current_rgb['G']} B={current_rgb['B']}")
    # print(f"  Dist_V: {dist_verde:.1f}, Dist_R: {dist_non_verde:.1f}, Dist_S: {dist_buio:.1f} -> {stato_piu_vicino}")

    return stato_piu_vicino


def main():
    # 1. Carica la configurazione del sensore
    calibrated_data = load_config(CALIBRAZIONE_FILE, "Calibrazione Sensore")
    if not calibrated_data:
        return  # Errore già stampato da load_config

    # Controlla che il file json sia valido
    if 'verde' not in calibrated_data or 'non_verde' not in calibrated_data or 'buio' not in calibrated_data:
        print("❌ Errore: Il file 'calibrazione.json' è incompleto.")
        print("   Assicurati che contenga le chiavi 'verde', 'non_verde' e 'buio'.")
        return

    # 2. Inizializza il sensore
    sensor = inizializza_sensore()
    if not sensor:
        return  # Errore già stampato

    # 3. Connetti a MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"❌ Errore critico MQTT: {e}")
        return

    print("🚀 Avvio monitoraggio con sensore TCS34725... (Premi Ctrl+C per fermare)")

    # Variabili per la logica di persistenza
    stato_pubblicato = None
    last_seen_color_time = 0

    try:
        while True:
            # 4. Leggi il sensore
            current_rgb = get_stabilized_rgb(sensor)

            # 5. Determina lo stato
            # Questa è la nuova funzione che sostituisce get_visual_status
            stato_rilevato = get_sensor_status(current_rgb, calibrated_data)

            # 6. Applica la logica di persistenza (identica alla versione webcam)
            stato_da_pubblicare = None
            if stato_rilevato != "SPENTO":
                # Se il colore è ROSSO o VERDE, aggiorna il timer
                last_seen_color_time = time.time()
                stato_da_pubblicare = stato_rilevato
            else:
                # Se il colore è SPENTO, controlla da quanto tempo non vediamo colori
                if time.time() - last_seen_color_time > STATE_PERSISTENCE_SECONDS:
                    stato_da_pubblicare = "SPENTO"
                else:
                    # Non è passato abbastanza tempo, manteniamo l'ultimo stato pubblicato
                    stato_da_pubblicare = stato_pubblicato

            # 7. Pubblica su MQTT (solo se lo stato cambia)
            if stato_da_pubblicare != stato_pubblicato:
                stato_pubblicato = stato_da_pubblicare
                timestamp = time.time()
                datetime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))

                payload = json.dumps({
                    "stato": stato_pubblicato,
                    "machine_id": MACHINE_ID,
                    "timestamp": timestamp,
                    "datetime_str": datetime_str
                })
                print(f"Stato Rilevato: {stato_rilevato} -> Stato Pubblicato: {stato_pubblicato}")
                client.publish(MQTT_TOPIC_STATUS, payload, qos=1, retain=True)

            # Pausa per non sovraccaricare il bus I2C e MQTT
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n🛑 Chiusura del programma...")
    finally:
        print("🧹 Rilascio risorse...")
        client.loop_stop()
        client.disconnect()
        print("✅ Programma terminato.")


if __name__ == "__main__":
    main()
