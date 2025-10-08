import cv2
import numpy as np
import json
import os

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
COLORS_TO_CALIBRATE = ["ROSSO", "GIALLO", "VERDE"]

# Costruisce il percorso corretto per il file di configurazione
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")


def nothing(x):
    pass


def calibrate():
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
        print(f"\n--- Calibrazione per il colore: {color_name} ---")
        print("1. Usa gli slider per isolare il colore nella finestra 'Mask'.")
        print("2. Quando sei soddisfatto, premi 's' per salvare e passare al colore successivo.")
        print("3. Premi 'q' per annullare l'intera operazione.")

        # Loop per la calibrazione del singolo colore
        while True:
            ret, frame = cap.read()
            if not ret:
                print("⚠️ Frame non ricevuto.")
                break

            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            h_min = cv2.getTrackbarPos("H Min", "Trackbars")
            h_max = cv2.getTrackbarPos("H Max", "Trackbars")
            s_min = cv2.getTrackbarPos("S Min", "Trackbars")
            s_max = cv2.getTrackbarPos("S Max", "Trackbars")
            v_min = cv2.getTrackbarPos("V Min", "Trackbars")
            v_max = cv2.getTrackbarPos("V Max", "Trackbars")

            lower_bound = np.array([h_min, s_min, v_min])
            upper_bound = np.array([h_max, s_max, v_max])

            mask = cv2.inRange(hsv, lower_bound, upper_bound)

            cv2.imshow(f"Originale - Calibra: {color_name}", frame)
            cv2.imshow("Mask", mask)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('s'):
                # Salva i valori per il colore corrente
                calibrated_data[color_name] = {
                    "lower": [h_min, s_min, v_min],
                    "upper": [h_max, s_max, v_max]
                }
                print(
                    f"✅ Valori per {color_name} salvati: lower={calibrated_data[color_name]['lower']}, upper={calibrated_data[color_name]['upper']}")
                break  # Esce dal loop del singolo colore

            if key == ord('q'):
                print("\n❌ Calibrazione annullata dall'utente.")
                cap.release()
                cv2.destroyAllWindows()
                return

    # Salva il file JSON
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(calibrated_data, f, indent=4)

    print(f"\n\n🎉 Calibrazione completata! I valori sono stati salvati in '{CONFIG_FILE}'.")

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    calibrate()

