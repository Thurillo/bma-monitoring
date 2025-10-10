import cv2
import numpy as np
import json
import os
import time

# --- CONFIGURAZIONE ---
CAMERA_INDEX = 0
STATES_TO_CALIBRATE = ["ROSSO", "VERDE", "SPENTO"]
RECORDING_SECONDS = 5
ANALYSIS_FRAME_COUNT = 30

# --- CONFIGURAZIONE LAYOUT DASHBOARD ---
PANEL_WIDTH = 250
THUMBNAIL_HEIGHT = 100
COLOR_SWATCH_HEIGHT = 50
PADDING = 10

# Percorsi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")


# --- Funzioni di supporto (load_roi, draw_text_with_background, record_and_analyze) ---
# Queste funzioni rimangono identiche alla versione precedente.
# Le includo per completezza, ma non ci sono modifiche qui.

def load_roi():
    if not os.path.exists(ROI_CONFIG_FILE):
        print(f"❌ Errore: File ROI '{ROI_CONFIG_FILE}' non trovato. Esegui prima configura_zona.py.")
        return None
    with open(ROI_CONFIG_FILE, 'r') as f:
        return json.load(f)


def draw_text_with_background(frame, text, position, font_scale=0.6, color=(255, 255, 255), bg_color=(0, 0, 0)):
    (text_width, text_height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
    x, y = position
    cv2.rectangle(frame, (x, y - text_height - baseline), (x + text_width + PADDING, y + PADDING), bg_color, -1)
    cv2.putText(frame, text, (x + 5, y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, 1, cv2.LINE_AA)


def get_activation_threshold(video_file, hsv_range):
    cap = cv2.VideoCapture(video_file)
    total_pixels = 0
    total_white_pixels = 0
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret: break
        frame_count += 1
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(hsv_range['lower']), np.array(hsv_range['upper']))
        total_pixels += frame.shape[0] * frame.shape[1]
        total_white_pixels += cv2.countNonZero(mask)
        cv2.imshow("Verifica Maschera (premi 'q')", mask)
        if cv2.waitKey(30) & 0xFF == ord('q'): break
    cap.release()
    cv2.destroyAllWindows()
    if frame_count == 0: return 10
    avg_percentage = (total_white_pixels / total_pixels) * 100
    suggested_threshold = max(5, int(avg_percentage * 0.75))
    print(
        f"\nNel video, il colore copre in media il {avg_percentage:.2f}%. Suggeriamo una soglia del {suggested_threshold}%.")
    try:
        user_input = input(f"Inserisci la soglia % (o INVIO per usare {suggested_threshold}): ")
        return int(user_input) if user_input else suggested_threshold
    except ValueError:
        return suggested_threshold


def record_and_analyze(state_name, main_roi_coords):
    temp_video_file = os.path.join(SCRIPT_DIR, f"temp_{state_name}.avi")
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened(): return None, None
    frame_width, frame_height = int(cap.get(3)), int(cap.get(4))
    fps = int(cap.get(5)) or 20
    out = cv2.VideoWriter(temp_video_file, cv2.VideoWriter_fourcc(*'XVID'), fps, (frame_width, frame_height))
    start_time = time.time()
    while time.time() - start_time < RECORDING_SECONDS:
        ret, frame = cap.read()
        if not ret: break
        countdown = RECORDING_SECONDS - int(time.time() - start_time)
        draw_text_with_background(frame, f"REG {state_name}: {countdown}s", (10, 30), color=(0, 0, 255))
        out.write(frame)
        cv2.imshow("Registrazione...", frame)
        cv2.waitKey(1)
    cap.release()
    out.release()
    cv2.destroyWindow("Registrazione...")
    cap = cv2.VideoCapture(temp_video_file)
    ret, first_frame = cap.read()
    if not ret: return None, None
    frame_for_selection = first_frame.copy()
    x, y, w, h = main_roi_coords['x'], main_roi_coords['y'], main_roi_coords['w'], main_roi_coords['h']
    cv2.rectangle(frame_for_selection, (x, y), (x + w, y + h), (0, 255, 0), 2)
    selection = cv2.selectROI("Seleziona Campione Puro", frame_for_selection)
    cv2.destroyWindow("Seleziona Campione Puro")
    if selection[2] == 0:
        os.remove(temp_video_file)
        return None, None
    sel_x, sel_y, sel_w, sel_h = selection
    cropped_video_file = os.path.join(SCRIPT_DIR, "temp_cropped.avi")
    out_crop = cv2.VideoWriter(cropped_video_file, cv2.VideoWriter_fourcc(*'XVID'), fps, (sel_w, sel_h))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    while True:
        ret, frame = cap.read()
        if not ret: break
        out_crop.write(frame[sel_y:sel_y + sel_h, sel_x:sel_x + sel_w])
    out_crop.release()
    cap.release()
    cap_crop = cv2.VideoCapture(cropped_video_file)
    hsv_data = []
    while True:
        ret, frame = cap_crop.read()
        if not ret: break
        hsv_area = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hsv_data.extend(hsv_area.reshape(-1, 3))
    cap_crop.release()
    if not hsv_data:
        os.remove(temp_video_file)
        os.remove(cropped_video_file)
        return None, None
    hsv_data = np.array(hsv_data)
    mean, std = np.mean(hsv_data, axis=0), np.std(hsv_data, axis=0)
    if state_name == "SPENTO":
        lower_bound = np.array([0, 0, 0])
        upper_bound = np.minimum([179, 255, 255], mean + std * 3).astype(int)
        upper_bound[1] = min(upper_bound[1], 80)
        upper_bound[2] = min(upper_bound[2], 80)
    else:
        lower_bound = np.maximum(0, mean - std * 1.5).astype(int)
        upper_bound = np.minimum([179, 255, 255], mean + std * 1.5).astype(int)
    hsv_range = {"lower": lower_bound.tolist(), "upper": upper_bound.tolist()}
    threshold = get_activation_threshold(cropped_video_file, hsv_range)
    hsv_range["threshold_percent"] = threshold
    os.remove(temp_video_file)
    os.remove(cropped_video_file)
    cropped_thumbnail = first_frame[y:y + h, x:x + w]
    return hsv_range, cropped_thumbnail


# <-- MODIFICA CHIAVE: Nuova funzione per la verifica live ---
def get_live_status(roi_frame, calibrated_data):
    """
    Analizza un frame in tempo reale e restituisce lo stato attivo
    basandosi sui dati già calibrati.
    """
    if not calibrated_data:
        return None

    hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
    total_pixels = roi_frame.shape[0] * roi_frame.shape[1]
    if total_pixels == 0: return None

    detected_colors = []

    for color_name, ranges in calibrated_data.items():
        lower_bound = np.array(ranges['lower'])
        upper_bound = np.array(ranges['upper'])
        threshold = ranges.get('threshold_percent', 10)

        mask = cv2.inRange(hsv_frame, lower_bound, upper_bound)
        pixel_count = cv2.countNonZero(mask)
        percentage = (pixel_count / total_pixels) * 100

        if percentage >= threshold:
            detected_colors.append({"name": color_name, "percentage": percentage})

    if not detected_colors:
        return None

    best_match = max(detected_colors, key=lambda x: x['percentage'])
    return best_match['name']


def main():
    roi = load_roi()
    if not roi: return

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("❌ Errore: Impossibile accedere alla webcam.")
        return

    frame_width, frame_height = int(cap.get(3)), int(cap.get(4))

    roi_w, roi_h = roi['w'], roi['h']
    panel_thumb_width = PANEL_WIDTH - 2 * PADDING
    panel_thumb_height = int(roi_h * (panel_thumb_width / roi_w))

    dashboard_width = frame_width + PANEL_WIDTH
    dashboard_height = frame_height

    calibrated_data = {}
    sample_thumbnails = {}

    print("--- Dashboard di Calibrazione e Verifica Live ---")

    while True:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.5)
            continue

        dashboard = np.zeros((dashboard_height, dashboard_width, 3), dtype=np.uint8)
        dashboard[0:frame_height, 0:frame_width] = frame

        x, y, w, h = roi['x'], roi['y'], roi['w'], roi['h']
        cv2.rectangle(dashboard, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(dashboard, "Campo Visivo", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        draw_text_with_background(dashboard, "Premi 'r', 'v', 's' per calibrare", (10, 30))
        draw_text_with_background(dashboard, "Premi 'q' per SALVARE e USCIRE", (10, 60))

        # <-- MODIFICA CHIAVE: Esegui la verifica live ad ogni frame ---
        roi_frame = frame[y:y + h, x:x + w]
        live_detected_state = get_live_status(roi_frame, calibrated_data)

        # --- Costruisci il pannello di stato a destra ---
        panel_x_start = frame_width
        panel = dashboard[0:dashboard_height, panel_x_start:dashboard_width]
        panel.fill(40)

        section_height = dashboard_height // len(STATES_TO_CALIBRATE)

        for i, state_name in enumerate(STATES_TO_CALIBRATE):
            section_y_start = i * section_height

            # Titolo
            title_pos = (PADDING, section_y_start + 20)
            cv2.putText(panel, state_name, title_pos, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # <-- MODIFICA CHIAVE: Disegna la spia di stato ---
            indicator_pos = (PANEL_WIDTH - PADDING - 10, section_y_start + 15)
            indicator_color = (80, 80, 80)  # Grigio spento
            if live_detected_state == state_name:
                # Se lo stato è attivo, prendi il colore medio calibrato per la spia
                if state_name in calibrated_data:
                    mean_hsv = np.uint8([[calibrated_data[state_name].get('mean_hsv', [0, 0, 220])]])
                    mean_bgr = cv2.cvtColor(mean_hsv, cv2.COLOR_HSV2BGR)[0][0]
                    indicator_color = (int(mean_bgr[0]), int(mean_bgr[1]), int(mean_bgr[2]))
                else:  # Fallback a giallo se non ancora calibrato
                    indicator_color = (0, 255, 255)

            cv2.circle(panel, indicator_pos, 10, indicator_color, -1)
            cv2.circle(panel, indicator_pos, 10, (255, 255, 255), 1)  # Bordo bianco

            # Thumbnail e Color Swatch (logica identica a prima)
            thumb_y_start = section_y_start + 30
            thumb_placeholder = panel[
                thumb_y_start:thumb_y_start + panel_thumb_height, PADDING:panel_thumb_width + PADDING]
            if state_name in sample_thumbnails:
                thumb = cv2.resize(sample_thumbnails[state_name],
                                   (thumb_placeholder.shape[1], thumb_placeholder.shape[0]))
                thumb_placeholder[:, :] = thumb
            else:
                thumb_placeholder[:, :] = (80, 80, 80)

            color_y_start = thumb_y_start + panel_thumb_height + PADDING
            color_placeholder = panel[color_y_start:color_y_start + COLOR_SWATCH_HEIGHT, PADDING:PANEL_WIDTH - PADDING]
            if state_name in calibrated_data:
                mean_hsv = np.uint8([[calibrated_data[state_name].get('mean_hsv', [0, 0, 80])]])
                mean_bgr = cv2.cvtColor(mean_hsv, cv2.COLOR_HSV2BGR)[0][0]
                color_placeholder[:, :] = (int(mean_bgr[0]), int(mean_bgr[1]), int(mean_bgr[2]))
            else:
                color_placeholder[:, :] = (80, 80, 80)

        cv2.imshow("Dashboard di Calibrazione e Verifica", dashboard)
        key = cv2.waitKey(1) & 0xFF

        state_to_record = None
        if key == ord('r'):
            state_to_record = "ROSSO"
        elif key == ord('v'):
            state_to_record = "VERDE"
        elif key == ord('s'):
            state_to_record = "SPENTO"
        elif key == ord('q'):
            break

        if state_to_record:
            cap.release()
            cv2.destroyWindow("Dashboard di Calibrazione e Verifica")
            hsv_range, thumbnail = record_and_analyze(state_to_record, roi)
            if hsv_range and thumbnail is not None:
                calibrated_data[state_to_record] = hsv_range
                sample_thumbnails[state_to_record] = thumbnail
            cap.open(CAMERA_INDEX)

    cap.release()
    cv2.destroyAllWindows()

    if len(calibrated_data) >= 2:  # Salva solo se almeno 2 stati sono stati calibrati
        final_data_to_save = {k: v for k, v in calibrated_data.items()}
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(COLOR_CONFIG_FILE, 'w') as f:
            json.dump(final_data_to_save, f, indent=4)
        print(f"\n🎉 Calibrazione salvata!")
    else:
        print("\nCalibrazione incompleta. File non salvato.")


if __name__ == "__main__":
    main()

