import cv2

CAMERA_INDEX = 0  # Prova a cambiare questo numero se hai più dispositivi

cap = cv2.VideoCapture(CAMERA_INDEX)

if not cap.isOpened():
    print(f"ERRORE: Impossibile aprire la camera all'indice {CAMERA_INDEX}")
    exit()

print("Camera aperta con successo. Premere 'q' per uscire.")

while True:
    # Cattura frame per frame
    ret, frame = cap.read()

    # Se ret è False, il frame non è stato letto correttamente
    if not ret:
        print("Impossibile ricevere il frame (stream terminato?). Esco...")
        break

    # Mostra il frame risultante
    cv2.imshow('Test Camera', frame)

    # Aspetta 1ms per un tasto, se è 'q' esci dal loop
    if cv2.waitKey(1) == ord('q'):
        break

# Rilascia tutto quando hai finito
cap.release()
cv2.destroyAllWindows()
print("Risorse rilasciate.")
