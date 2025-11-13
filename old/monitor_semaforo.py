import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os
import argparse
from collections import deque

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0

# --- CONFIGURAZIONE LOGICA DI RILEVAMENTO ---
# <-- MODIFICA CHIAVE: Aumentato a 3 secondi come richiesto
STATE_PERSISTENCE_SECONDS = 3.0 
# Buffer per stabilizzare il rilevamento del colore dominante
STABILITY_BUFFER_SIZE = 15 

# --- CONFIGURAZIONE MQTT (e Percorsi) ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
MACHINE_ID = "macchina_01" 
MQTT_TOPIC_STATUS = f"bma/{MACHINE_ID}/semaforo/stato"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")

# --- Funzioni di supporto (load_config, on_connect, etc.) ---
# Omesse per brevità nel commento, ma presenti nel codice completo.
def load_config(file_path, config_name):
    if not os.path.exists(file_path): return None
    with open(file_path, 'r') as f: return json.load(f)

def on_connect(client, userdata, flags, rc, properties):
    if rc == 0: print("✅ Connesso!")
    else: print(f"❌ Connessione fallita: {rc}.")

def on_disconnect(client, userdata, flags, reason_code, properties):
    print(f"⚠️ Disconnesso: {reason_code}.")

def get_visual_status(roi_frame, color_ranges):
    if roi_frame is None or roi_frame.size == 0: return "SPENTO", {}
    hsv = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
    total_pixels = roi_frame.shape[0] * roi_frame.shape[1]
    details = {}
    detected = []
    for name, ranges in color_ranges.items():
        if 'threshold_percent' not in ranges: return "ERRORE_CONFIG", {}
        lower, upper, thresh = np.array(ranges['lower']), np.array(ranges['upper']), ranges['threshold_percent']
        mask = cv2.inRange(hsv, lower, upper)
        perc = (cv2.countNonZero(mask) / total_pixels) * 100
        details[name] = {'percentage': perc, 'threshold': thresh}
        if name != "SPENTO" and perc >= thresh:
            detected.append({"name": name, "percentage": perc})
    if not detected: return "SPENTO", details
    return max(detected, key=lambda x: x['percentage'])['name'], details

def draw_debug_overlay(frame, details, roi_coords, stabilized_state):
    x, y, w, h = roi_coords['x'], roi_coords['y'], roi_coords['w'], roi_coords['h']
    cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
    y_offset = 30
    for name, data in details.items():
        perc, thresh = data['percentage'], data['threshold']
        text = f"{name}: {perc:.1f}% (>{thresh}%)"
        color = (0, 255, 0) if perc >= thresh else (0, 0, 255)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(frame, (5, y_offset - th - 5), (10 + tw, y_offset + 5), (0,0,0), -1)
        cv2.putText(frame, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)
        y_offset += 25
    # Mostra lo stato stabilizzato per un debug migliore
    cv2.putText(frame, f"Stato Stabile: {stabilized_state}", (10, frame.shape[0] - 20), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

def main(debug=False):
    roi = load_config(ROI_CONFIG_FILE, "ROI")
    color_ranges = load_config(COLOR_CONFIG_FILE, "Colori")
    if not roi or not color_ranges: return
    
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    
    try: client.connect(MQTT_BROKER, MQTT_PORT, 60); client.loop_start()
    except Exception as e: print(f"❌ Errore MQTT: {e}"); return
    
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened(): print("❌ Errore Webcam."); client.loop_stop(); return
    
    print("🚀 Avvio monitoraggio... (Premi Ctrl+C per fermare)")

    stato_pubblicato = None
    last_seen_color_time = 0
    visual_state_buffer = deque(maxlen=STABILITY_BUFFER_SIZE)

    try:
        while True:
            ret, frame = cap.read()
            if not ret: time.sleep(0.1); continue
            
            x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
            roi_frame = frame[y:y+h, x:x+w]
            
            stato_corrente_visivo, detection_details = get_visual_status(roi_frame, color_ranges)
            visual_state_buffer.append(stato_corrente_visivo)

            # --- NUOVA LOGICA DI STATO DOMINANTE ---
            rosso_count = visual_state_buffer.count("ROSSO")
            verde_count = visual_state_buffer.count("VERDE")

            stato_stabile_corrente = "SPENTO"
            if rosso_count > verde_count:
                stato_stabile_corrente = "ROSSO"
            elif verde_count > rosso_count:
                stato_stabile_corrente = "VERDE"
            
            # --- LOGICA DI PERSISTENZA BASATA SULLO STATO DOMINANTE ---
            stato_da_pubblicare = None
            if stato_stabile_corrente != "SPENTO":
                # Se il colore dominante è ROSSO o VERDE, aggiorna il timer
                last_seen_color_time = time.time()
                stato_da_pubblicare = stato_stabile_corrente
            else:
                # Se il colore dominante è SPENTO, controlla da quanto tempo non vediamo colori
                if time.time() - last_seen_color_time > STATE_PERSISTENCE_SECONDS:
                    stato_da_pubblicare = "SPENTO"
                else:
                    # Non è passato abbastanza tempo, manteniamo l'ultimo stato pubblicato
                    stato_da_pubblicare = stato_pubblicato
            
            if stato_da_pubblicare != stato_pubblicato:
                stato_pubblicato = stato_da_pubblicare
                timestamp = time.time()
                datetime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
                
                payload = json.dumps({
                    "stato": stato_pubblicato, "machine_id": MACHINE_ID,
                    "timestamp": timestamp, "datetime_str": datetime_str
                })
                print(f"Stato Pubblicato: {stato_pubblicato}. Invio messaggio MQTT...")
                client.publish(MQTT_TOPIC_STATUS, payload, qos=1, retain=True)
            
            if debug:
                draw_debug_overlay(frame, detection_details, roi, stato_stabile_corrente)
                cv2.imshow("Live Feed con Debug", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'): break
            else:
                time.sleep(0.1)
                
    except KeyboardInterrupt: print("\n🛑 Chiusura del programma...")
    finally:
        print("🧹 Rilascio risorse...")
        cap.release(); client.loop_stop(); client.disconnect()
        if debug: cv2.destroyAllWindows()
        print("✅ Programma terminato.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitora lo stato di un semaforo via webcam.")
    parser.add_argument("--debug", action="store_true", help="Mostra un overlay di debug sul video.")
    args = parser.parse_args()
    main(debug=args.debug)



