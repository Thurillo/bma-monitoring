import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os
import argparse
from collections import deque
from datetime import datetime  # <-- AGGIUNTO: Modulo per gestire date e ore

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
MACHINE_ID = "macchina_01"
# Costruisce i percorsi corretti per i file di configurazione
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")

# --- CONFIGURAZIONE MQTT ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
MQTT_TOPIC_BASE = "bma"
MQTT_TOPIC = f"{MQTT_TOPIC_BASE}/{MACHINE_ID}/semaforo/stato"

# --- Parametri per la gestione del lampeggio ---
SPENTO_PERSISTENCE_SECONDS = 1.5
HISTORY_WINDOW_SECONDS = 2.0
PIXEL_THRESHOLD = 150


# --- FUNZIONI DI SUPPORTO ---
def load_config(file_path, config_name):
    if not os.path.exists(file_path):
        print(f"❌ Errore: File '{file_path}' non trovato.")
        print(f"➡️  Esegui prima lo script di configurazione per '{config_name}'!")
        return None
    with open(file_path, 'r') as f:
        return json.load(f)


# --- CALLBACK MQTT ---
def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        print("✅ Connesso con successo al broker MQTT!")
    else:
        print(f"❌ Connessione fallita, codice: {rc}. Controlla IP, porta, utente e password.")


def on_disconnect(client, userdata, flags, rc, properties):
    print("🔌 Disconnesso dal broker MQTT. Tenterò di riconnettermi...")


# --- LOGICA PRINCIPALE ---
def main(debug_mode=False):
    # Caricamento configurazioni
    roi = load_config(ROI_CONFIG_FILE, "ROI")
    color_ranges = load_config(COLOR_CONFIG_FILE, "colori")
    if not roi or not color_ranges:
        return

    # Inizializzazione client MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    try:
        print(f"🔗 Tentativo di connessione al broker MQTT: {MQTT_BROKER}...")
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"❌ Errore critico di connessione MQTT: {e}")
        return

    # Inizializzazione webcam e variabili di stato
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    print("🚀 Avvio monitoraggio semaforo... (Premi Ctrl+C per fermare)")

    stato_storia = deque()
    stato_precedente_pubblicato = None

    try:
        while True:
            current_time = time.time()
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.5)
                continue

            # 1. Rilevazione stato istantaneo
            x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
            roi_frame = frame[y:y + h, x:x + w]
            hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)

            stato_istantaneo = "SPENTO"
            detected_mask = np.zeros(roi_frame.shape[:2], dtype="uint8")

            for color_name, ranges in color_ranges.items():
                lower = np.array(ranges["lower"])
                upper = np.array(ranges["upper"])
                mask = cv2.inRange(hsv_frame, lower, upper)
                if cv2.countNonZero(mask) > PIXEL_THRESHOLD:
                    stato_istantaneo = color_name
                    detected_mask = mask
                    break

            # 2. Aggiornamento della storia degli stati
            stato_storia.append((current_time, stato_istantaneo))
            while stato_storia and stato_storia[0][0] < current_time - HISTORY_WINDOW_SECONDS:
                stato_storia.popleft()

            # 3. Determina lo stato finale da pubblicare
            ultimo_colore_visto = "SPENTO"
            tempo_ultimo_colore = 0
            for t, s in reversed(stato_storia):
                if s != "SPENTO":
                    ultimo_colore_visto = s
                    tempo_ultimo_colore = t
                    break

            if current_time - tempo_ultimo_colore < SPENTO_PERSISTENCE_SECONDS:
                stato_finale = ultimo_colore_visto
            else:
                stato_finale = "SPENTO"

            # 4. Pubblica su MQTT solo se lo stato finale è cambiato
            if stato_finale != stato_precedente_pubblicato:
                # --- MODIFICA: Creazione del payload con data leggibile ---
                datetime_obj = datetime.fromtimestamp(current_time)
                #datetime_string = datetime_obj.strftime('%H:%M:%S-%d:%m:%Y')  # Formato richiesto
                datetime_string = datetime_obj.strftime('%Y:%m:%d %H:%M:%S')  # Formato richiesto

                print(f"Stato cambiato: {stato_precedente_pubblicato} -> {stato_finale}. Invio messaggio MQTT...")

                payload = json.dumps({
                    "stato": stato_finale,
                    "timestamp": current_time,
                    "datetime_str": datetime_string  # <-- NUOVO CAMPO
                })
                # --- FINE MODIFICA ---

                client.publish(MQTT_TOPIC, payload, qos=1, retain=True)
                stato_precedente_pubblicato = stato_finale

            # 5. Debug visivo (se attivato)
            if debug_mode:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(frame, f"Stato Pubblicato: {stato_finale}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (255, 255, 255), 2)
                cv2.imshow("Live Feed con ROI", frame)
                cv2.imshow("Maschera Colore Rilevato", detected_mask)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                time.sleep(0.1)

    finally:
        print("\n🛑 Fermo il monitoraggio e pulisco le risorse...")
        cap.release()
        client.loop_stop()
        client.disconnect()
        cv2.destroyAllWindows()
        print("Uscita pulita.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitoraggio semaforo per macchine BMA.")
    parser.add_argument("--debug", action="store_true", help="Abilita le finestre di debug visivo.")
    args = parser.parse_args()
    main(args.debug)