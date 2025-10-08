import cv2
import numpy as np
import json
import os
import time

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
COLORS_TO_CALIBRATE = ["ROSSO", "VERDE"]
# <-- AGGIUNTO: Tolleranze per l'impostazione automatica degli slider
HUE_TOLERANCE = 10  # Tolleranza per la Tonalità (più stretta)
SV_TOLERANCE = 40  # Tolleranza per Saturazione/Luminosità (più ampia)

# Percorsi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")

# --- AGGIUNTO: Variabile globale per passare il frame alla funzione del mouse ---
hsv_roi_for_callback = None


def nothing(x):
    pass


def load_roi():
    if not os.path.exists(ROI_CONFIG_FILE):
        print(f"❌ Errore: File ROI '{ROI_CONFIG_FILE}' non trovato. Esegui prima configura_zona.py.")
        return None
    with open(ROI_CONFIG_FILE, 'r') as f:
        return json.load(f)


# --- AGGIUNTO: Funzione "Ispettore di Pixel" attivata dal click del mouse ---
def inspect_pixel(event, x, y, flags, param):
    global hsv_roi_for_callback
    if event == cv2.EVENT_LBUTTONDOWN:  # Se viene premuto il tasto sinistro del mouse
        if hsv_roi_for_callback is not None:
            pixel_hsv = hsv_roi_for_callback[y, x]
            h, s, v = pixel_hsv[0], pixel_hsv[1], pixel_hsv[2]

            print("\n--- 🔍 Pixel Ispezionato ---")
            print(f"   Valori HSV: H={h}, S={s}, V={v}")
            print("   -> Imposto gli slider su un intervallo attorno a questi valori...")

            # Calcola i nuovi range per gli slider
            h_min = max(0, h - HUE_TOLERANCE)
            h_max = min(179, h + HUE_TOLERANCE)
            s_min = max(0, s - SV_TOLERANCE)
            s_max = min(255, s + SV_TOLERANCE)
            v_min = max(0, v - SV_TOLERANCE)
            v_max = min(255, v + SV_TOLERANCE)

            # Imposta le posizioni delle trackbar
            cv2.setTrackbarPos("H Min", "Trackbars", h_min)
            cv2.setTrackbarPos("H Max", "Trackbars", h_max)
            cv2.setTrackbarPos("S Min", "Trackbars", s_min)
            cv2.setTrackbarPos("S Max", "Trackbars", s_max)
            cv2.setTrackbarPos("V Min", "Trackbars", v_min)
            cv2.setTrackbarPos("V Max", "Trackbars", v_max)
            print("--- Slider Aggiornati! ---")


def calibrate():
    global hsv_roi_for_callback
    roi = load_roi()
    if not roi: return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    cv2.namedWindow("Trackbars")
    cv2.createTrackbar("H Min", "Trackbars", 0, 179, nothing)
    cv2.createTrackbar("H Max", "Trackbars", 179, 179, nothing)
    cv2.createTrackbar("S Min", "Trackbars", 0, 255, nothing)
    cv2.createTrackbar("S Max", "Trackbars", 255, 255, nothing)
    cv2.createTrackbar("V Min", "Trackbars", 0, 255, nothing)
    cv2.createTrackbar("V Max", "Trackbars", 255, 255, nothing)

    calibrated_data = {}

    for color_name in COLORS_TO_CALIBRATE:
        window_name = f"ROI - Clicca per ispezionare '{color_name}'"
        cv2.namedWindow(window_name)
        cv2.setMouseCallback(window_name, inspect_pixel)  # Associa la funzione del mouse alla finestra

        print(f"\n--- Calibrazione per il colore: {color_name} ---")
        print("   -> Lavorando solo sulla zona (ROI) preselezionata.")
        print(
            "💡 AIUTO: Clicca con il mouse sul colore desiderato nella finestra ROI per impostare automaticamente gli slider!")
        print("1. Clicca sul colore o imposta gli slider per isolare il colore nella finestra 'Mask'.")
        print("2. Quando sei soddisfatto, premi 's' per salvare e passare al successivo.")
        print("3. Premi 'q' per annullare.")

        while True:
            ret, frame = cap.read()
            if not ret: break

            x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
            roi_frame = frame[y:y + h, x:x + w]
            if roi_frame.size == 0: continue

            hsv = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
            hsv_roi_for_callback = hsv.copy()  # Aggiorna la variabile globale

            h_min, h_max, s_min, s_max, v_min, v_max = (
                cv2.getTrackbarPos("H Min", "Trackbars"), cv2.getTrackbarPos("H Max", "Trackbars"),
                cv2.getTrackbarPos("S Min", "Trackbars"), cv2.getTrackbarPos("S Max", "Trackbars"),
                cv2.getTrackbarPos("V Min", "Trackbars"), cv2.getTrackbarPos("V Max", "Trackbars")
            )

            lower_bound = np.array([h_min, s_min, v_min])
            upper_bound = np.array([h_max, s_max, v_max])
            mask = cv2.inRange(hsv, lower_bound, upper_bound)

            cv2.imshow(window_name, roi_frame)
            cv2.imshow("Mask", mask)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('s'):
                calibrated_data[color_name] = {"lower": [h_min, s_min, v_min], "upper": [h_max, s_max, v_max]}
                print(f"✅ Valori per {color_name} salvati.")
                cv2.destroyWindow(window_name)
                break

            if key == ord('q'):
                print("\n❌ Calibrazione annullata.")
                cap.release()
                cv2.destroyAllWindows()
                return

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(COLOR_CONFIG_FILE, 'w') as f:
        json.dump(calibrated_data, f, indent=4)

    print(f"\n\n🎉 Calibrazione completata! Valori salvati in '{COLOR_CONFIG_FILE}'.")
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    calibrate()

