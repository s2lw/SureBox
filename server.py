from flask import Flask, request, jsonify
from flask_cors import CORS
from RPLCD.i2c import CharLCD
import pigpio
import threading
from time import sleep
import RPi.GPIO as GPIO
import sqlite3
import random
import hashlib  # do generowania tokenu
from functools import wraps

app = Flask(__name__)
CORS(app)

DB_NAME = "lockers.db"
LOCKERS = []
ROWS = [17, 27, 22, 23]
COLS = [5, 6, 13, 19]
KEYPAD = [
    ["1", "2", "3", "A"],
    ["4", "5", "6", "B"],
    ["7", "8", "9", "C"],
    ["*", "0", "#", "D"]
]

lcd = None
pi = None

# ========== Dekorator autentykacji (Bearer token) ==========
def require_auth(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Brak Bearer tokenu"}), 401
        token = auth_header.replace("Bearer ", "")
        user = get_user_by_token(token)
        if not user:
            return jsonify({"error": "Nieprawidlowy token"}), 401
        request.current_user = user
        return func(*args, **kwargs)
    return wrapper

# ========== Inicjalizacja bazy i wczytanie do LOCKERS ==========

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # Tabela users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            token TEXT,
            code TEXT
        )
    """)

    # Tabela lockers
    c.execute("""
        CREATE TABLE IF NOT EXISTS lockers (
            id INTEGER PRIMARY KEY,
            servo_pin INTEGER,
            sensor_pin INTEGER,
            status TEXT,
            occupied BOOLEAN,
            closed BOOLEAN,
            owner_id INTEGER
        )
    """)
    conn.commit()

    # Przykladowy user, jesli brak
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users (username,password,code) VALUES (?,?,?)", ("adam", "pass", "1111"))
        c.execute("INSERT INTO users (username,password,code) VALUES (?,?,?)", ("ewa", "pass", "2222"))
        conn.commit()

    # Przykladowe lockers, jesli brak
    c.execute("SELECT COUNT(*) FROM lockers")
    if c.fetchone()[0] == 0:
        default_lockers = [
            (0, 7, 1,   'locked', True,  True,  1),
            (1, 21, 20, 'locked', False, True,  None),
            (2, 15, 14, 'unlocked', False, False, None),
            (3, 26, 12, 'unlocked', False, False, None),
        ]
        c.executemany("""
            INSERT INTO lockers (id, servo_pin, sensor_pin, status, occupied, closed, owner_id)
            VALUES (?,?,?,?,?,?,?)
        """, default_lockers)
        conn.commit()

    # Wczytanie lockers do listy LOCKERS w Pythonie
    c.execute("""
        SELECT id, servo_pin, sensor_pin, status, occupied, closed, owner_id
        FROM lockers
        ORDER BY id
    """)
    rows = c.fetchall()
    conn.close()

    for row in rows:
        LOCKERS.append({
            "servo_pin": row[1],
            "sensor_pin": row[2],
            "status": row[3],
            "occupied": bool(row[4]),
            "closed": bool(row[5]),
            "owner_id": row[6],
            "sensor_closed": False
        })

def update_locker_in_db(locker_id):
    locker = LOCKERS[locker_id]
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        UPDATE lockers
        SET status=?, occupied=?, closed=?, owner_id=?
        WHERE id=?
    """, (
        locker["status"],
        locker["occupied"],
        locker["closed"],
        locker["owner_id"],
        locker_id
    ))
    conn.commit()
    conn.close()

# ========== Obsługa tokenów i haseł ==========

def generate_token(username):
    raw = f"{username}{random.randint(1000,9999)}"
    return hashlib.md5(raw.encode()).hexdigest()

def get_user_by_token(token):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, username, token FROM users WHERE token=?", (token,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "username": row[1], "token": row[2]}
    return None

# ========== Sterowanie serwem i czujnikami ==========

def set_angle(angle, servo_pin):
    pulse = 500 + (angle/180)*2000
    pi.set_servo_pulsewidth(servo_pin, pulse)

def unlock_locker(locker_id):
    locker = LOCKERS[locker_id]
    set_angle(130, locker["servo_pin"])
    locker["status"] = "unlocked"
    locker["closed"] = False
    update_locker_in_db(locker_id)
    lcd.clear()
    lcd.write_string(f"Szafka {locker_id+1}\notwarta")
    sleep(2)
    lcd.clear()

def lock_locker(locker_id):
    locker = LOCKERS[locker_id]
    set_angle(30, locker["servo_pin"])
    locker["status"] = "locked"
    locker["closed"] = True
    update_locker_in_db(locker_id)
    lcd.clear()
    lcd.write_string(f"Szafka {locker_id+1}\nzamknieta")
    sleep(2)
    lcd.clear()

def sensor_thread():
    while True:
        for i, locker in enumerate(LOCKERS):
            closed = (GPIO.input(locker["sensor_pin"]) == GPIO.HIGH)
            locker["sensor_closed"] = closed
        sleep(0.3)

import sqlite3

DB_NAME = "lockers.db"

def check_code(entered_code, locker_id):
    """
    Sprawdza, czy kod 'entered_code' jest poprawny
    dla uzytkownika (owner_id) tej szafki.
    Zwraca True/False.
    """
    locker = LOCKERS[locker_id]

    # Czy w ogóle jest zajęta
    if not locker["occupied"]:
        return False

    # Kto jest wlaścicielem
    user_id = locker["owner_id"]
    if user_id is None:
        return False

    # Pobieramy z bazy kod usera
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT code FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()

    if row is None:
        return False  # nie ma takiego usera w bazie

    actual_code = row[0]  # code z bazy
    return (actual_code == entered_code)



def read_keypad():
    for row_index, row in enumerate(ROWS):
        GPIO.output(row, GPIO.HIGH)
        for col_index, col in enumerate(COLS):
            if GPIO.input(col) == GPIO.HIGH:
                GPIO.output(row, GPIO.LOW)
                return KEYPAD[row_index][col_index]
        GPIO.output(row, GPIO.LOW)
    return None


def keypad_thread():
    current_menu = "main"
    action = None  # "open" lub "close"
    selected_locker = None
    entered_code = ""
    last_displayed_message = None

    def update_lcd(message):
        nonlocal last_displayed_message
        if message != last_displayed_message:
            lcd.clear()
            lines = message.split("\n")[:2]
            for i, line in enumerate(lines):
                lcd.cursor_pos = (i, 0)
                lcd.write_string(line.ljust(16))
            last_displayed_message = message

    while True:
        if current_menu == "main":
            update_lcd("Menu:\nA=Open B=Close")

        elif current_menu == "select_locker":
            if action == "open":
                update_lcd("Otworz:\n1-4 #=back")
            else:
                update_lcd("Zamknij:\n1-4 #=back")

        elif current_menu == "enter_code":
            # Ograniczamy np. do 4 cyfr
            disp_code = entered_code[:4]
            update_lcd(f"L:{selected_locker+1}\nK:{disp_code}")

        key = read_keypad()
        if key:
            # ========== MAIN ==========
            if current_menu == "main":
                if key == "A":
                    action = "open"
                    current_menu = "select_locker"
                elif key == "B":
                    action = "close"
                    current_menu = "select_locker"
                else:
                    # np. "#"
                    pass

            # ========== SELECT LOCKER ==========
            elif current_menu == "select_locker":
                if key in "1234":
                    sel = int(key)-1
                    if sel<0 or sel>=len(LOCKERS):
                        update_lcd("Brak takiej\nszafki!")
                        sleep(1)
                        current_menu="main"
                    else:
                        selected_locker=sel
                        # Sprawdz stan logiczny
                        locker=LOCKERS[selected_locker]
                        if action=="open":
                            # Jesli juz unlocked?
                            if locker["status"]=="unlocked":
                                update_lcd("Juz otwarta")
                                sleep(1)
                                current_menu="main"
                            else:
                                entered_code=""
                                current_menu="enter_code"
                        else:
                            # close
                            if locker["status"]=="locked":
                                update_lcd("Juz zamknieta")
                                sleep(1)
                                current_menu="main"
                            else:
                                lock_locker(selected_locker)
                                update_lcd(f"Sz.{selected_locker+1}\nzamknieta")
                                sleep(1)
                                current_menu="main"
                elif key=="#":
                    current_menu="main"
                else:
                    update_lcd("Zly klaw.\n1-4,#=back")
                    sleep(1)

            # ========== ENTER CODE (tylko open) ==========
            elif current_menu=="enter_code":
                if key in "0123456789":
                    # Dodajemy cyfre, np. ogranicz do 4
                    if len(entered_code)<4:
                        entered_code += key
                elif key=="A":
                    # potwierdz
                    if check_code(entered_code, selected_locker):
                        unlock_locker(selected_locker)
                        update_lcd(f"Sz.{selected_locker+1}\notwarta!")
                        sleep(1)
                    else:
                        update_lcd("Zly kod!")
                        sleep(1)
                    current_menu="main"
                elif key=="B":
                    # backspace
                    if entered_code:
                        entered_code=entered_code[:-1]
                elif key=="#":
                    current_menu="main"
                else:
                    update_lcd("Zly klaw.\n0-9,A,B,#")
                    sleep(1)

        sleep(0.1)


# ========== Endpointy Flask ==========

@app.route('/register', methods=['POST'])
def register():
    if not request.is_json:
        return {"error": "Expect JSON"}, 400
    data = request.get_json()
    user = data.get("username")
    pwd = data.get("password")
    if not user or not pwd:
        return {"error": "Missing user/pass"}, 400

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users(username,password) VALUES(?,?)", (user, pwd))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return {"error": "User exists"}, 400
    conn.close()
    return {"message": "OK"}, 200

@app.route('/login', methods=['POST'])
def login():
    if not request.is_json:
        return {"error": "Expect JSON"}, 400
    data = request.get_json()
    user = data.get("username")
    pwd = data.get("password")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, password FROM users WHERE username=?", (user,))
    row = c.fetchone()
    if not row:
        conn.close()
        return {"error": "Wrong user/pass"}, 401
    uid, dbpass = row
    if dbpass != pwd:
        conn.close()
        return {"error": "Wrong user/pass"}, 401
    token = generate_token(user)
    c.execute("UPDATE users SET token=? WHERE id=?", (token, uid))
    conn.commit()
    conn.close()
    return {"token": token}, 200

@app.route('/lockers', methods=['GET'])
def get_lockers():
    data = []
    for i, lk in enumerate(LOCKERS):
        data.append({
            "id": i,
            "status": lk["status"],
            "occupied": lk["occupied"],
            "closed": lk["closed"],
            "sensor_closed": lk["sensor_closed"],
            "owner_id": lk["owner_id"]
        })
    return {"lockers": data}, 200

@app.route('/lockers/<int:locker_id>/unlock', methods=['POST'])
@require_auth
def unlock_endpoint(locker_id):
    user = request.current_user
    # sprawdzmy w tym miejscu, czy user -> owner, itp.
    locker = LOCKERS[locker_id]
    if locker["owner_id"] != user["id"]:
        return {"error": "Brak dostepu"}, 403

    # tu też ewentualnie sprawdz sensor / code
    # jeżeli OK:
    unlock_locker(locker_id)
    return {"success": True, "message": "Otwarta"}, 200



@app.route('/lockers/<int:locker_id>/lock', methods=['POST'])
def lock_endpoint(locker_id):
    """
    Teraz pozwalamy zamknac szafke nawet wtedy, gdy sensor_closed = False.
    Zmieniamy tylko logike w tym endpointcie.
    """
    if locker_id < 0 or locker_id >= len(LOCKERS):
        return {"success": False, "message": "Zly locker ID"}, 400

    locker = LOCKERS[locker_id]

    if locker["status"] == "locked":
        return {"success": False, "message": "Szafka juz zamknieta"}, 400
    else:
        # Jesli jest 'unlocked', lock_locker niezaleznie od sensor_closed
        lock_locker(locker_id)
        return {"success": True, "message": "Zamknieto"}, 200

@app.route('/lockers/<int:locker_id>/return', methods=['POST'])
@require_auth
def return_locker(locker_id):
    """
    Zwraca (oddaje) szafke, jesli user jest jej wlascicielem (owner_id).
    Jesli szafka jest locked, to faktycznie wywolujemy 'unlock_locker' -
    a nie tylko ustawiamy 'status=unlocked'.
    """
    user = request.current_user
    if locker_id < 0 or locker_id >= len(LOCKERS):
        return jsonify({"success": False, "message": "Zly locker ID"}), 400

    locker = LOCKERS[locker_id]

    # Czy jest zajeta i wlasnosc bieżącego usera
    if not locker["occupied"] or locker["owner_id"] != user["id"]:
        return jsonify({"success": False, "message": "Nie masz dostepu do tej szafki albo juz wolna"}), 403

    # Jesli jest locked => najpierw faktycznie otwieramy
    if locker["status"] == "locked":
        unlock_locker(locker_id)  # To faktycznie wykona set_angle(...) i ustawi status=unlocked, closed=False

    # Teraz logicznie zwalniamy szafke
    locker["occupied"] = False
    locker["owner_id"] = None
    # Nie zmieniamy statusu "unlocked" recznie - bo 'unlock_locker' juz to zrobil
    # (jesli faktycznie trzeba bylo)
    update_locker_in_db(locker_id)

    return jsonify({"success": True,
                    "message": f"Szafka {locker_id+1} zwrocona i wolna"}), 200


@app.route('/lockers/deposit', methods=['POST'])
@require_auth
def deposit():
    """
    Rezerwacja (zajecie) konkretnej szafki przez zalogowanego usera.
    - user = request.current_user
    - Dopuszczamy deposit niezaleznie od sensor_closed i statusu
    - Ustawiamy occupied=True, owner_id=user['id'], status=unlocked, closed=False
    """
    user = request.current_user
    if not request.is_json:
        return jsonify({"success": False, "message": "Expect JSON"}), 400

    data = request.get_json()
    locker_id = data.get("locker_id")
    if locker_id is None:
        return jsonify({"success": False, "message": "No locker_id"}), 400

    if locker_id < 0 or locker_id >= len(LOCKERS):
        return jsonify({"success": False, "message": "Invalid locker ID"}), 400

    locker = LOCKERS[locker_id]
    if locker["occupied"]:
        return jsonify({"success": False, "message": "Locker already occupied"}), 400

    locker["occupied"] = True
    locker["owner_id"] = user["id"]
    locker["status"] = "unlocked"
    locker["closed"] = False
    update_locker_in_db(locker_id)

    return jsonify({
        "success": True,
        "message": f"Locker {locker_id+1} reserved & open for user {user['username']}",
        "locker_id": locker_id,
        "owner_id": user["id"]
    }), 200


# ========== Główna pętla ==========

if __name__ == "__main__":
    init_db()
    pi = pigpio.pi()
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    for i, lk in enumerate(LOCKERS):
        GPIO.setup(lk["sensor_pin"], GPIO.IN, pull_up_down=GPIO.PUD_UP)

    lcd = CharLCD(
        i2c_expander='PCF8574',
        address=0x3f,
        port=1,
        cols=16,
        rows=2
    )

    for r in ROWS:
        GPIO.setup(r, GPIO.OUT)
        GPIO.output(r, GPIO.LOW)
    for c in COLS:
        GPIO.setup(c, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    t_sens = threading.Thread(target=sensor_thread, daemon=True)
    t_sens.start()

    t_key = threading.Thread(target=keypad_thread, daemon=True)
    t_key.start()

    try:
        app.run(host="0.0.0.0", port=5000)
    except KeyboardInterrupt:
        GPIO.cleanup()
        pi.stop()