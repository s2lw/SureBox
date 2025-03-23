import requests
from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup

API_URL = "http://192.168.1.27:5000"  # Adres Twojego serwera Flask


class LoginScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)

        self.username_input = TextInput(hint_text="Username", multiline=False)
        self.password_input = TextInput(hint_text="Password", password=True, multiline=False)

        login_button = Button(text="Login", size_hint=(1, 0.2))
        login_button.bind(on_press=self.do_login)

        register_button = Button(text="Go to Register", size_hint=(1, 0.2))
        register_button.bind(on_press=lambda x: setattr(self.manager, 'current', 'register'))

        layout.add_widget(Label(text="Login Screen", font_size=24))
        layout.add_widget(self.username_input)
        layout.add_widget(self.password_input)
        layout.add_widget(login_button)
        layout.add_widget(register_button)

        self.add_widget(layout)

    def do_login(self, instance):
        username = self.username_input.text.strip()
        password = self.password_input.text.strip()

        if not username or not password:
            self.show_popup("Error", "Please enter username & password")
            return

        try:
            resp = requests.post(f"{API_URL}/login", json={"username": username, "password": password})
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                if token:
                    # Przejście do ekranu głównego
                    main_screen = self.manager.get_screen("main")
                    main_screen.token = token

                    self.manager.current = "main"
                else:
                    self.show_popup("Error", "No token in response")
            else:
                try:
                    data = resp.json()
                    msg = data.get("error") or data.get("message") or resp.text
                    self.show_popup("Error", f"{resp.status_code}: {msg}")
                except:
                    self.show_popup("Error", f"{resp.status_code}: {resp.text}")
        except requests.RequestException as e:
            self.show_popup("Error", str(e))

    def show_popup(self, title, msg):
        popup = Popup(title=title, content=Label(text=msg), size_hint=(0.7, 0.7))
        popup.open()


class RegisterScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)

        self.username_input = TextInput(hint_text="Username", multiline=False)
        self.password_input = TextInput(hint_text="Password", password=True, multiline=False)
        self.code_input = TextInput(hint_text="4-digit code", multiline=False)

        reg_button = Button(text="Register", size_hint=(1, 0.2))
        reg_button.bind(on_press=self.do_register)

        back_button = Button(text="Back to Login", size_hint=(1, 0.2))
        back_button.bind(on_press=lambda x: setattr(self.manager, 'current', 'login'))

        layout.add_widget(Label(text="Register Screen", font_size=24))
        layout.add_widget(self.username_input)
        layout.add_widget(self.password_input)
        layout.add_widget(self.code_input)
        layout.add_widget(reg_button)
        layout.add_widget(back_button)

        self.add_widget(layout)

    def do_register(self, instance):
        username = self.username_input.text.strip()
        password = self.password_input.text.strip()
        code = self.code_input.text.strip()

        if not username or not password:
            self.show_popup("Error", "Please enter username & password")
            return

        if len(code) != 4 or not code.isdigit():
            self.show_popup("Error", "Please enter exactly 4 digits in code")
            return

        try:
            resp = requests.post(
                f"{API_URL}/register",
                json={"username": username, "password": password, "code": code}
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", "Registered")
                self.show_popup("Success", msg)
            else:
                try:
                    data = resp.json()
                    err = data.get("error") or data.get("message") or resp.text
                    self.show_popup("Error", f"{resp.status_code}: {err}")
                except:
                    self.show_popup("Error", f"{resp.status_code}: {resp.text}")
        except requests.RequestException as e:
            self.show_popup("Error", str(e))

    def show_popup(self, title, msg):
        popup = Popup(title=title, content=Label(text=msg), size_hint=(0.7, 0.7))
        popup.open()


class MainScreen(Screen):
    """
    Glowny ekran do zarzadzania szafkami
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.token = None

        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)

        self.status_label = Label(text="Status: Ready", size_hint=(1, 0.1))
        layout.add_widget(self.status_label)

        self.refresh_button = Button(text="Refresh Lockers", size_hint=(1, 0.1))
        self.refresh_button.bind(on_press=self.refresh_lockers)
        layout.add_widget(self.refresh_button)

        self.locker_box = BoxLayout(orientation="vertical", spacing=5, size_hint=(1, 0.7))
        layout.add_widget(self.locker_box)

        logout_button = Button(text="Logout", size_hint=(1, 0.1))
        logout_button.bind(on_press=self.logout)
        layout.add_widget(logout_button)

        self.add_widget(layout)

    def logout(self, instance):
        self.token = None
        self.manager.current = "login"

    def refresh_lockers(self, instance=None):
        self.locker_box.clear_widgets()
        if not self.token:
            self.status_label.text = "Not logged in"
            return

        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = requests.get(f"{API_URL}/lockers", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                lockers = data["lockers"]
                for locker in lockers:
                    locker_id = locker["id"]
                    occ = "Occupied" if locker["occupied"] else "Available"
                    sensor = "Closed" if locker["sensor_closed"] else "Open"
                    owner = locker.get("owner_id")
                    text = f"Locker {locker_id + 1} - {occ}, sensor={sensor}, owner={owner}"

                    btn = Button(text=text, size_hint=(1, None), height=50)
                    btn.bind(on_press=lambda x, lid=locker_id: self.show_actions(lid))
                    self.locker_box.add_widget(btn)
                self.status_label.text = "Lockers refreshed"
            else:
                self.show_error(resp)
        except requests.RequestException as e:
            self.status_label.text = f"Network error: {str(e)}"

    def show_actions(self, locker_id):
        layout = BoxLayout(orientation="vertical", padding=10)

        reserve_btn = Button(text="Reserve & Open", size_hint=(1, None), height=40)
        reserve_btn.bind(on_press=lambda i: self.reserve_and_open(locker_id))
        layout.add_widget(reserve_btn)

        open_btn = Button(text="Unlock (open)", size_hint=(1, None), height=40)
        open_btn.bind(on_press=lambda i: self.open_locker(locker_id))
        layout.add_widget(open_btn)

        return_btn = Button(text="Return Locker", size_hint=(1, None), height=40)
        return_btn.bind(on_press=lambda i: self.return_locker(locker_id))
        layout.add_widget(return_btn)

        close_btn = Button(text="Close Locker (anyone)", size_hint=(1, None), height=40)
        close_btn.bind(on_press=lambda i: self.close_locker(locker_id))
        layout.add_widget(close_btn)

        cancel_btn = Button(text="Cancel", size_hint=(1, None), height=40)
        layout.add_widget(cancel_btn)

        popup = Popup(title=f"Locker {locker_id + 1} Actions", content=layout, size_hint=(0.8, 0.8))
        cancel_btn.bind(on_press=lambda i: popup.dismiss())
        popup.open()

    def reserve_and_open(self, locker_id):
        if not self.token:
            self.status_label.text = "Not logged in"
            return

        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = requests.post(
                f"{API_URL}/lockers/deposit",
                headers=headers,
                json={"locker_id": locker_id}
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", "Reserved & Opened")
                self.status_label.text = msg
                self.refresh_lockers()
            else:
                self.show_error(resp)
        except requests.RequestException as e:
            self.status_label.text = f"Network error: {str(e)}"

    def open_locker(self, locker_id):
        if not self.token:
            self.status_label.text = "Not logged in"
            return
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = requests.post(f"{API_URL}/lockers/{locker_id}/unlock", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", "Opened")
                self.status_label.text = msg
                self.refresh_lockers()
            else:
                self.show_error(resp)
        except requests.RequestException as e:
            self.status_label.text = f"Network error: {str(e)}"

    def return_locker(self, locker_id):
        if not self.token:
            self.status_label.text = "Not logged in"
            return
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = requests.post(f"{API_URL}/lockers/{locker_id}/return", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", "Returned")
                self.status_label.text = msg
                self.refresh_lockers()
            else:
                self.show_error(resp)
        except requests.RequestException as e:
            self.status_label.text = f"Network error: {str(e)}"

    def close_locker(self, locker_id):
        try:
            resp = requests.post(f"{API_URL}/lockers/{locker_id}/lock")
            if resp.status_code == 200:
                data = resp.json()
                msg = data.get("message", "Locker closed")
                self.status_label.text = msg
                self.refresh_lockers()
            else:
                self.show_error(resp)
        except requests.RequestException as e:
            self.status_label.text = f"Network error: {str(e)}"

    def show_error(self, resp):
        try:
            data = resp.json()
            err = data.get("message") or data.get("error") or resp.text
            self.status_label.text = f"Error {resp.status_code}: {err}"
        except:
            self.status_label.text = f"Error {resp.status_code}: {resp.text}"


class LockerManagementApp(App):
    def build(self):
        sm = ScreenManager()
        sm.add_widget(LoginScreen(name="login"))
        sm.add_widget(RegisterScreen(name="register"))
        sm.add_widget(MainScreen(name="main"))
        return sm


if __name__ == "__main__":
    LockerManagementApp().run()