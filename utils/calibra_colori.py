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
# <-- MODIFICA CHIAVE: Valore minimo di luminosità per uno stato "ACCESO"
MIN_BRIGHTNESS_FOR_ON_STATE = 100

# --- CONFIGURAZIONE LAYOUT DASHBOARD ---
PANEL_WIDTH = 250
PADDING = 10
TITLE_AREA_HEIGHT = 40

# Percorsi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(SCRIPT_DIR, "..", "config")
COLOR_CONFIG_FILE = os.path.join(CONFIG_DIR, "color_ranges.json")
ROI_CONFIG_FILE = os.path.join(CONFIG_DIR, "roi_semaforo.json")


# Funzioni di supporto (load_roi, draw_text_with_background, etc. sono identiche)
def load_roi():
    if not os.path.exists(ROI_CONFIG_FILE): return None
    with open(ROI_CONFIG_FILE, 'r') as f: return json.load(f)


def draw_text_with_background(frame, text, pos, scale=0.6, color=(255, 255, 255), bg=(0, 0, 0)):
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = pos
    cv2.rectangle(frame, (x, y - h - base), (x + w + PADDING, y + PADDING), bg, -1)
    cv2.putText(frame, text, (x + 5, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def get_activation_threshold(video_file, hsv_range):
    cap = cv2.VideoCapture(video_file)
    total_pixels, white_pixels, frame_count = 0, 0, 0
    while True:
        ret, frame = cap.read()
        if not ret: break
        frame_count += 1
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(hsv_range['lower']), np.array(hsv_range['upper']))
        total_pixels += frame.size;
        white_pixels += cv2.countNonZero(mask)
        cv2.imshow("Verifica Maschera (premi 'q')", mask)
        if cv2.waitKey(30) & 0xFF == ord('q'): break
    cap.release();
    cv2.destroyAllWindows()
    if frame_count == 0: return 10
    avg_perc = (white_pixels / total_pixels) * 100
    sugg_thresh = max(5, int(avg_perc * 0.75))
    print(f"\nMedia copertura colore: {avg_perc:.2f}%. Soglia suggerita: {sugg_thresh}%.")
    try:
        user_input = input(f"Inserisci soglia % (INVIO per {sugg_thresh}): ")
        return int(user_input) if user_input else sugg_thresh
    except ValueError:
        return sugg_thresh


def record_and_analyze(state_name, main_roi_coords):
    temp_video_file = os.path.join(SCRIPT_DIR, f"temp_{state_name}.avi")
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened(): return None, None
    w, h, fps = int(cap.get(3)), int(cap.get(4)), int(cap.get(5)) or 20
    out = cv2.VideoWriter(temp_video_file, cv2.VideoWriter_fourcc(*'XVID'), fps, (w, h))
    start_time = time.time()
    while time.time() - start_time < RECORDING_SECONDS:
        ret, frame = cap.read()
        if not ret: break
        countdown = RECORDING_SECONDS - int(time.time() - start_time)
        draw_text_with_background(frame, f"REG {state_name}: {countdown}s", (10, 30), color=(0, 0, 255))
        out.write(frame);
        cv2.imshow("Registrazione...", frame);
        cv2.waitKey(1)
    cap.release();
    out.release();
    cv2.destroyWindow("Registrazione...")
    cap = cv2.VideoCapture(temp_video_file)
    ret, first_frame = cap.read()
    if not ret: return None, None
    frame_sel = first_frame.copy()
    x, y, w, h = main_roi_coords['x'], main_roi_coords['y'], main_roi_coords['w'], main_roi_coords['h']
    cv2.rectangle(frame_sel, (x, y), (x + w, y + h), (0, 255, 0), 2)
    selection = cv2.selectROI("Seleziona Campione Puro", frame_sel)
    cv2.destroyWindow("Seleziona Campione Puro")
    if selection[2] == 0: os.remove(temp_video_file); return None, None
    sel_x, sel_y, sel_w, sel_h = selection
    cropped_video_file = os.path.join(SCRIPT_DIR, "temp_cropped.avi")
    out_crop = cv2.VideoWriter(cropped_video_file, cv2.VideoWriter_fourcc(*'XVID'), fps, (sel_w, sel_h))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    while True:
        ret, frame = cap.read()
        if not ret: break
        out_crop.write(frame[sel_y:sel_y + sel_h, sel_x:sel_x + sel_w])
    out_crop.release();
    cap.release()
    cap_crop = cv2.VideoCapture(cropped_video_file)
    hsv_data = []
    while True:
        ret, frame = cap_crop.read()
        if not ret: break
        hsv_area = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hsv_data.extend(hsv_area.reshape(-1, 3))
    cap_crop.release()
    if not hsv_data: os.remove(temp_video_file); os.remove(cropped_video_file); return None, None
    hsv_data = np.array(hsv_data)
    mean, std = np.mean(hsv_data, axis=0), np.std(hsv_data, axis=0)

    # --- MODIFICA CHIAVE: Logica con vincoli di luminosità ---
    if state_name == "SPENTO":
        # Per lo stato SPENTO, forza bassa saturazione e luminosità
        lower_bound = np.array([0, 0, 0])
        upper_bound = np.minimum([179, 255, 255], mean + std * 3).astype(int)
        upper_bound[1] = min(upper_bound[1], 80)  # Saturazione bassa
        upper_bound[2] = min(upper_bound[2], 80)  # Luminosità bassa
    else:  # Per ROSSO e VERDE
        # Calcola normalmente ma poi imposta un vincolo di luminosità minima
        lower_bound = np.maximum(0, mean - std * 1.5).astype(int)
        upper_bound = np.minimum([179, 255, 255], mean + std * 1.5).astype(int)
        # Forza il valore minimo di luminosità per essere considerato "ACCESO"
        lower_bound[2] = max(lower_bound[2], MIN_BRIGHTNESS_FOR_ON_STATE)
        print(f"    -> Vincolo luminosità minima ({MIN_BRIGHTNESS_FOR_ON_STATE}) applicato per lo stato {state_name}.")

    hsv_range = {"lower": lower_bound.tolist(), "upper": upper_bound.tolist(), "mean_hsv": mean.astype(int).tolist()}
    threshold = get_activation_threshold(cropped_video_file, hsv_range)
    hsv_range["threshold_percent"] = threshold
    os.remove(temp_video_file);
    os.remove(cropped_video_file)
    cropped_thumbnail = first_frame[y:y + h, x:x + w]
    return hsv_range, cropped_thumbnail


# La funzione get_live_status e main rimangono identiche alla versione precedente
# Sono omesse qui per brevità ma sono nel codice completo.
def get_live_status(roi_frame, calibrated_data):
    if not calibrated_data or roi_frame is None or roi_frame.size == 0: return None
    hsv_frame = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
    total_pixels = roi_frame.shape[0] * roi_frame.shape[1]
    detected_colors = []
    for color_name, ranges in calibrated_data.items():
        if color_name == "SPENTO": continue
        lower, upper = np.array(ranges['lower']), np.array(ranges['upper'])
        threshold = ranges.get('threshold_percent', 10)
        mask = cv2.inRange(hsv_frame, lower, upper)
        percentage = (cv2.countNonZero(mask) / total_pixels) * 100
        if percentage >= threshold:
            detected_colors.append({"name": color_name, "percentage": percentage})
    if not detected_colors: return "SPENTO"
    return max(detected_colors, key=lambda x: x['percentage'])['name']


def main():
    roi = load_roi()
    if not roi: return
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened(): return
    w, h = int(cap.get(3)), int(cap.get(4))
    roi_w, roi_h = roi['w'], roi['h']
    panel_thumb_w = PANEL_WIDTH - 2 * PADDING
    panel_thumb_h = int(roi_h * (panel_thumb_w / roi_w))
    dash_w, dash_h = w + PANEL_WIDTH, h
    calibrated_data, sample_thumbnails = {}, {}
    TITLE_COLORS = {"ROSSO": (0, 0, 255), "VERDE": (0, 255, 0), "SPENTO": (255, 255, 255)}
    print("--- Dashboard di Calibrazione e Verifica Live ---")
    while True:
        ret, frame = cap.read()
        if not ret: time.sleep(0.5); continue
        dashboard = np.zeros((dash_h, dash_w, 3), dtype=np.uint8)
        dashboard[0:h, 0:w] = frame
        x, y, w_roi, h_roi = roi['x'], roi['y'], roi['w'], roi['h']
        cv2.rectangle(dashboard, (x, y), (x + w_roi, y + h_roi), (0, 255, 0), 2)
        cv2.putText(dashboard, "Campo Visivo", (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        draw_text_with_background(dashboard, "Premi 'r', 'v', 's' per calibrare", (10, 30))
        draw_text_with_background(dashboard, "Premi 'q' per SALVARE", (10, 60))
        roi_frame = frame[y:y + h_roi, x:x + w_roi]
        live_state = get_live_status(roi_frame, calibrated_data)
        panel = dashboard[0:dash_h, w:dash_w]
        panel.fill(40)
        section_h = dash_h // len(STATES_TO_CALIBRATE)
        for i, state_name in enumerate(STATES_TO_CALIBRATE):
            sec_y = i * section_h
            title_area = panel[sec_y: sec_y + TITLE_AREA_HEIGHT]
            title_area[:] = (60, 60, 60)
            title_y_pos = sec_y + (TITLE_AREA_HEIGHT // 2) + 5
            cv2.putText(panel, state_name, (PADDING, title_y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        TITLE_COLORS.get(state_name), 2)
            ind_pos = (PANEL_WIDTH - PADDING - 15, title_y_pos - 5)
            ind_color = (80, 80, 80)
            if live_state == state_name:
                if state_name in calibrated_data:
                    mean_hsv = np.uint8([[calibrated_data[state_name].get('mean_hsv', [0, 0, 220])]])
                    mean_bgr = cv2.cvtColor(mean_hsv, cv2.COLOR_HSV2BGR)[0][0]
                    ind_color = tuple(map(int, mean_bgr))
                else:
                    ind_color = (0, 255, 255)
            cv2.circle(panel, ind_pos, 10, ind_color, -1)
            cv2.circle(panel, ind_pos, 10, (255, 255, 255), 1)
            content_y = sec_y + TITLE_AREA_HEIGHT + PADDING
            thumb_ph = panel[content_y: content_y + panel_thumb_h, PADDING: PADDING + panel_thumb_w]
            if state_name in sample_thumbnails:
                thumb_ph[:, :] = cv2.resize(sample_thumbnails[state_name], (thumb_ph.shape[1], thumb_ph.shape[0]))
            else:
                thumb_ph[:, :] = (80, 80, 80)
            color_y = content_y + panel_thumb_h + PADDING
            color_swatch_h = 50
            color_ph = panel[color_y: color_y + color_swatch_h, PADDING: PANEL_WIDTH - PADDING]
            if state_name in calibrated_data:
                mean_hsv = np.uint8([[calibrated_data[state_name].get('mean_hsv', [0, 0, 80])]])
                mean_bgr = cv2.cvtColor(mean_hsv, cv2.COLOR_HSV2BGR)[0][0]
                color_ph[:, :] = tuple(map(int, mean_bgr))
            else:
                color_ph[:, :] = (80, 80, 80)
        cv2.imshow("Dashboard Calibrazione", dashboard)
        key = cv2.waitKey(1) & 0xFF
        state_to_rec = None
        if key == ord('r'):
            state_to_rec = "ROSSO"
        elif key == ord('v'):
            state_to_rec = "VERDE"
        elif key == ord('s'):
            state_to_rec = "SPENTO"
        elif key == ord('q'):
            break
        if state_to_rec:
            cap.release();
            cv2.destroyWindow("Dashboard Calibrazione")
            hsv_range, thumb = record_and_analyze(state_to_rec, roi)
            if hsv_range and thumb is not None:
                calibrated_data[state_to_rec] = hsv_range
                sample_thumbnails[state_to_rec] = thumb
            cap.open(CAMERA_INDEX)
    cap.release();
    cv2.destroyAllWindows()
    if len(calibrated_data) >= 2:
        final_data = {k: {k2: v2 for k2, v2 in v.items() if k2 != 'mean_hsv'} for k, v in calibrated_data.items()}
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(COLOR_CONFIG_FILE, 'w') as f:
            json.dump(final_data, f, indent=4)
        print("\n🎉 Calibrazione salvata!")
    else:
        print("\nCalibrazione incompleta, file non salvato.")


if __name__ == "__main__":
    main()