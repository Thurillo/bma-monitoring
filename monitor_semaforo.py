import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os
import argparse

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
STATE_PERSISTENCE_SECONDS = 1.5

# --- CONFIGURAZIONE MQTT ---
MQTT_BROKER = "192.168.20.163"
MQTT_PORT = 1883
MQTT_USERNAME = "shima"
MQTT_PASSWORD = "shima"
MACHINE_ID = "macchina_01"
MQTT_TOPIC_STATUS = f"bma/{MACHINE_ID}/semaforo/stato"

# Percorsi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "config")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")


def load_config(file_path, config_name):
    if not os.path.exists(file_path):
        print(f"❌ Errore: File di configurazione '{config_name}' non trovato.")
        return None
    with open(file_path, 'r') as f:
        return json.load(f)


def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
        print("✅ Connesso con successo al broker MQTT!")
    else:
        print(f"❌ Connessione fallita, codice: {rc}.")


# <-- MODIFICA CHIAVE: Correzione del bug MQTT ---
# La firma della funzione ora accetta 5 argomenti, come richiesto dalla libreria
def on_disconnect(client, userdata, flags, reason_code, properties):
    print(f"⚠️ Disconnesso dal broker MQTT con codice: {reason_code}.")


def get_visual_status(roi_frame, color_ranges):
    if roi_frame is None or roi_frame.size == 0: return "SPENTO", {}
    hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
    total_pixels = roi_frame.shape[0] * roi_frame.shape[1]

    detection_details = {}
    detected_colors = []

    for color_name, ranges in color_ranges.items():
        lower = np.array(ranges['lower'])
        upper = np.array(ranges['upper'])
        threshold = ranges.get('threshold_percent', 10)
        mask = cv2.inRange(hsv_frame, lower, upper)
        percentage = (cv2.countNonZero(mask) / total_pixels) * 100

        # Salva i dettagli per il debug overlay
        detection_details[color_name] = {'percentage': percentage, 'threshold': threshold}

        if color_name != "SPENTO" and percentage >= threshold:
            detected_colors.append({"name": color_name, "percentage": percentage})

    if not detected_colors:
        return "SPENTO", detection_details

    best_match = max(detected_colors, key=lambda x: x['percentage'])
    return best_match['name'], detection_details


def draw_debug_overlay(frame, details, roi_coords):
    """Disegna le informazioni di debug direttamente sul frame video."""
    # Disegna il rettangolo della ROI
    x, y, w, h = roi_coords['x'], roi_coords['y'], roi_coords['w'], roi_coords['h']
    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
    cv2.putText(frame, "Campo Visivo", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    # Scrivi le percentuali di rilevamento
    y_offset = 30
    for name, data in details.items():
        perc = data['percentage']
        thresh = data['threshold']
        text = f"{name}: {perc:.1f}% (>{thresh}%)"
        color = (0, 255, 0) if perc >= thresh else (0, 0, 255)

        # Sfondo per leggibilità
        (text_width, text_height), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(frame, (5, y_offset - text_height - 5), (10 + text_width, y_offset + 5), (0, 0, 0), -1)
        cv2.putText(frame, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)
        y_offset += 25


def main(debug=False):
    roi = load_config(ROI_CONFIG_FILE, "ROI")
    color_ranges = load_config(COLOR_CONFIG_FILE, "Colori")
    if not roi or not color_ranges: return

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"❌ Errore critico di connessione MQTT: {e}")
        return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        client.loop_stop()
        return

    print("🚀 Avvio monitoraggio... (Premi Ctrl+C per fermare)")

    stato_pubblicato = None
    last_seen_color_time = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue

            x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
            roi_frame = frame[y:y + h, x:x + w]

            stato_corrente_visivo, detection_details = get_visual_status(roi_frame, color_ranges)

            stato_da_pubblicare = None
            if stato_corrente_visivo != "SPENTO":
                last_seen_color_time = time.time()
                stato_da_pubblicare = stato_corrente_visivo
            else:
                if time.time() - last_seen_color_time > STATE_PERSISTENCE_SECONDS:
                    stato_da_pubblicare = "SPENTO"
                else:
                    stato_da_pubblicare = stato_pubblicato

            if stato_da_pubblicare != stato_pubblicato:
                stato_pubblicato = stato_da_pubblicare
                timestamp = time.time()
                datetime_str = time.strftime('%H:%M:%S-%d:%m:%Y', time.localtime(timestamp))
                payload = json.dumps({"stato": stato_pubblicato, "timestamp": timestamp, "datetime_str": datetime_str})
                print(f"Stato cambiato: {stato_pubblicato}. Invio messaggio MQTT...")
                client.publish(MQTT_TOPIC_STATUS, payload, qos=1, retain=True)

            if debug:
                # <-- MODIFICA CHIAVE: Disegna l'overlay direttamente sul frame principale ---
                draw_debug_overlay(frame, detection_details, roi)
                cv2.imshow("Live Feed con Debug", frame)

                if cv2.waitKey(1) & 0xFF == ord('q'): break
            else:
                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n🛑 Chiusura del programma in corso...")
    finally:
        print("🧹 Rilascio delle risorse...")
        cap.release()
        client.loop_stop()
        client.disconnect()
        if debug: cv2.destroyAllWindows()
        print("✅ Programma terminato.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitora lo stato di un semaforo via webcam.")
    parser.add_argument("--debug", action="store_true", help="Mostra un overlay di debug sul video.")
    args = parser.parse_args()
    main(debug=args.debug)

