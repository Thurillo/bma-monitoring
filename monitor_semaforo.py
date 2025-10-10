import cv2
import numpy as np
import paho.mqtt.client as mqtt
import json
import time
import os
import argparse

# ... (Configurazioni e percorsi identici a prima)
# Ometto per brevità, ma il codice completo è qui
CAMERA_INDEX = 0
STATE_PERSISTENCE_SECONDS = 1.5
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

        # <-- MODIFICA CHIAVE: Rimuovi il default, ora la soglia DEVE esistere
        if 'threshold_percent' not in ranges:
            print(f"ERRORE: Soglia mancante per '{color_name}' in color_ranges.json!")
            # In un sistema reale potresti voler gestire questo errore in modo diverso
            return "ERRORE_CONFIG", {}
        threshold = ranges['threshold_percent']

        mask = cv2.inRange(hsv_frame, lower, upper)
        percentage = (cv2.countNonZero(mask) / total_pixels) * 100

        detection_details[color_name] = {'percentage': percentage, 'threshold': threshold}

        if color_name != "SPENTO" and percentage >= threshold:
            detected_colors.append({"name": color_name, "percentage": percentage})

    if not detected_colors:
        return "SPENTO", detection_details

    best_match = max(detected_colors, key=lambda x: x['percentage'])
    return best_match['name'], detection_details


# ... (Il resto dello script main e __main__ è identico a prima)
def draw_debug_overlay(frame, details, roi_coords):
    x, y, w, h = roi_coords['x'], roi_coords['y'], roi_coords['w'], roi_coords['h']
    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
    y_offset = 30
    for name, data in details.items():
        perc, thresh = data['percentage'], data['threshold']
        text = f"{name}: {perc:.1f}% (>{thresh}%)"
        color = (0, 255, 0) if perc >= thresh else (0, 0, 255)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(frame, (5, y_offset - th - 5), (10 + tw, y_offset + 5), (0, 0, 0), -1)
        cv2.putText(frame, text, (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)
        y_offset += 25


def main(debug=False):
    roi = load_config(ROI_CONFIG_FILE, "ROI")
    color_ranges = load_config(COLOR_CONFIG_FILE, "Colori")
    if not roi or not color_ranges: return
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=MACHINE_ID)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_start()
    except Exception as e:
        print(f"❌ Errore critico di connessione MQTT: {e}");
        return
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.");
        client.loop_stop();
        return
    print("🚀 Avvio monitoraggio... (Premi Ctrl+C per fermare)")
    stato_pubblicato, last_seen_color_time = None, 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret: time.sleep(0.1); continue
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

