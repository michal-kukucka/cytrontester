#!/usr/bin/env python3
"""
Cytron Motordriver Tester
Laptop-side controller for Arduino UNO + Cytron MDDS30 bridge
(see ermes_mdds30_arduino_pwm_dir_spec.txt).

Cross-platform: macOS / Windows / Linux.
Requires: Python 3.8+, tkinter (bundled), pyserial.

    pip install pyserial
    python motor_control.py
"""

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    raise SystemExit("pyserial missing. Install with: pip install pyserial")

BAUD = 115200
HEARTBEAT_S = 0.1          # resend active command every 100 ms (watchdog is 300 ms)
ARDUINO_RESET_WAIT_S = 2.0  # UNO resets when serial port opens


class SerialLink:
    """Thread-safe serial connection with reader thread and heartbeat."""

    def __init__(self, rx_queue):
        self.rx_queue = rx_queue          # lines from Arduino -> GUI
        self.ser = None
        self.lock = threading.Lock()      # guards writes
        self.left = 0                     # last commanded values (heartbeat resends)
        self.right = 0
        self.moving = False
        self._stop_threads = threading.Event()
        self._reader = None
        self._heartbeat = None

    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    def connect(self, port):
        self.ser = serial.Serial(port, BAUD, timeout=0.2)
        self.rx_queue.put(("info", f"Opened {port}, waiting for Arduino reset..."))
        time.sleep(ARDUINO_RESET_WAIT_S)
        self.ser.reset_input_buffer()

        self._stop_threads.clear()
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat.start()

        # Spec: default to STOP after connecting, then PING.
        self.send_stop()
        self.send_line("PING")

    def disconnect(self):
        if self.connected:
            try:
                self.send_stop()
            except serial.SerialException:
                pass
        self._stop_threads.set()
        if self.ser:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
        self.ser = None
        self.moving = False
        self.left = 0
        self.right = 0

    def send_line(self, line):
        if not self.connected:
            return
        with self.lock:
            self.ser.write((line + "\n").encode("ascii"))
        self.rx_queue.put(("tx", line))

    def send_move(self, left, right):
        left = max(-100, min(100, int(left)))
        right = max(-100, min(100, int(right)))
        self.left = left
        self.right = right
        self.moving = (left != 0 or right != 0)
        self.send_line(f"M L={left} R={right}")

    def send_stop(self):
        self.moving = False
        self.left = 0
        self.right = 0
        self.send_line("STOP")

    def _heartbeat_loop(self):
        while not self._stop_threads.is_set():
            time.sleep(HEARTBEAT_S)
            if self.connected and self.moving:
                try:
                    with self.lock:
                        self.ser.write(f"M L={self.left} R={self.right}\n".encode("ascii"))
                except serial.SerialException:
                    self.rx_queue.put(("error", "Serial write failed. Motors unsafe — check hardware."))
                    self._stop_threads.set()

    def _reader_loop(self):
        while not self._stop_threads.is_set():
            try:
                raw = self.ser.readline()
            except (serial.SerialException, AttributeError, TypeError):
                if not self._stop_threads.is_set():
                    self.rx_queue.put(("error", "Serial read failed — disconnected?"))
                break
            if raw:
                line = raw.decode("ascii", errors="replace").strip()
                if line:
                    self.rx_queue.put(("rx", line))


class MotorPanel(ttk.LabelFrame):
    """Direction + velocity controls for one motor."""

    def __init__(self, parent, title, on_change):
        super().__init__(parent, text=title, padding=10)
        self.on_change = on_change

        self.direction = tk.IntVar(value=1)   # +1 forward, -1 reverse
        self.velocity = tk.IntVar(value=0)    # 0..100

        dir_frame = ttk.Frame(self)
        dir_frame.pack(fill="x")
        ttk.Radiobutton(dir_frame, text="Forward", variable=self.direction,
                        value=1, command=self._changed).pack(side="left", padx=5)
        ttk.Radiobutton(dir_frame, text="Reverse", variable=self.direction,
                        value=-1, command=self._changed).pack(side="left", padx=5)

        self.value_label = ttk.Label(self, text="0 %", font=("TkDefaultFont", 16, "bold"))
        self.value_label.pack(pady=(8, 0))

        self.slider = ttk.Scale(self, from_=0, to=100, orient="horizontal",
                                command=self._slider_moved)
        self.slider.pack(fill="x", pady=5)

        btns = ttk.Frame(self)
        btns.pack()
        for v in (0, 25, 50, 75, 100):
            ttk.Button(btns, text=str(v), width=4,
                       command=lambda v=v: self.set_velocity(v)).pack(side="left", padx=2)

    def _slider_moved(self, raw):
        self.velocity.set(int(float(raw)))
        self._changed()

    def set_velocity(self, v):
        self.velocity.set(v)
        self.slider.set(v)
        self._changed()

    def _changed(self):
        self.value_label.config(text=f"{self.signed_percent():+d} %".replace("+0", "0"))
        self.on_change()

    def signed_percent(self):
        return self.direction.get() * self.velocity.get()

    def reset(self):
        self.velocity.set(0)
        self.slider.set(0)
        self.value_label.config(text="0 %")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Cytron Motordriver Tester")
        self.minsize(560, 600)

        self.rx_queue = queue.Queue()
        self.link = SerialLink(self.rx_queue)
        self.pending_send = None   # debounce timer id
        self.ramp_job = None       # after() id of active ramp tick

        self._build_connection_bar()
        self._build_motor_panels()
        self._build_ramp_controls()
        self._build_action_buttons()
        self._build_log()

        self.refresh_ports()
        self.after(50, self._poll_rx_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<space>", lambda e: self.stop_all())

    # --- UI construction ---------------------------------------------------

    def _build_connection_bar(self):
        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")

        ttk.Label(bar, text="Port:").pack(side="left")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(bar, textvariable=self.port_var, width=28,
                                       state="readonly")
        self.port_combo.pack(side="left", padx=5)

        ttk.Button(bar, text="Refresh", command=self.refresh_ports).pack(side="left")
        self.connect_btn = ttk.Button(bar, text="Connect", command=self.toggle_connect)
        self.connect_btn.pack(side="left", padx=5)

        self.status_label = ttk.Label(bar, text="Disconnected", foreground="red")
        self.status_label.pack(side="left", padx=10)

    def _build_motor_panels(self):
        motors = ttk.Frame(self, padding=(10, 0))
        motors.pack(fill="x")
        motors.columnconfigure(0, weight=1)
        motors.columnconfigure(1, weight=1)

        self.left_panel = MotorPanel(motors, "Left motor", self._on_setting_changed)
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.right_panel = MotorPanel(motors, "Right motor", self._on_setting_changed)
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

    def _build_ramp_controls(self):
        frame = ttk.LabelFrame(self, text="Ramp (linear accel / decel)", padding=10)
        frame.pack(fill="x", padx=10, pady=(10, 0))

        ttk.Label(frame, text="Time (s):").pack(side="left")
        self.ramp_time_var = tk.StringVar(value="3.0")
        ttk.Spinbox(frame, textvariable=self.ramp_time_var, from_=0.1, to=120,
                    increment=0.5, width=6).pack(side="left", padx=(5, 15))

        ttk.Button(frame, text="Accelerate 0 → set speed",
                   command=self.start_accel).pack(side="left", padx=5)
        ttk.Button(frame, text="Decelerate → 0",
                   command=self.start_decel).pack(side="left", padx=5)

        self.ramp_status = ttk.Label(frame, text="idle", foreground="#666666")
        self.ramp_status.pack(side="left", padx=10)

    def _build_action_buttons(self):
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="x")

        self.live_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Live update (send on change)",
                        variable=self.live_var).pack(side="left")

        ttk.Button(frame, text="Send", command=self.send_current).pack(side="left", padx=10)

        stop = tk.Button(frame, text="STOP  (Space)", command=self.stop_all,
                         bg="#cc0000", fg="white", activebackground="#ff2222",
                         activeforeground="white", font=("TkDefaultFont", 14, "bold"),
                         height=2)
        stop.pack(side="right", fill="x", expand=True, padx=(10, 0))

    def _build_log(self):
        frame = ttk.LabelFrame(self, text="Serial log", padding=5)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log = tk.Text(frame, height=10, state="disabled", wrap="none",
                           font=("Courier", 11))
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

        self.log.tag_config("tx", foreground="#0055cc")
        self.log.tag_config("rx", foreground="#007700")
        self.log.tag_config("error", foreground="#cc0000")
        self.log.tag_config("info", foreground="#666666")

    # --- Connection --------------------------------------------------------

    def refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            # Prefer likely Arduino ports.
            preferred = [p for p in ports if "usbmodem" in p or "usbserial" in p
                         or "ttyACM" in p or "ttyUSB" in p or p.startswith("COM")]
            self.port_var.set(preferred[0] if preferred else ports[0])
        self.log_line("info", f"Ports: {', '.join(ports) if ports else 'none found'}")

    def toggle_connect(self):
        if self.link.connected:
            self.link.disconnect()
            self._set_connected(False)
            self.log_line("info", "Disconnected.")
            return

        port = self.port_var.get()
        if not port:
            self.log_line("error", "No port selected.")
            return
        self.connect_btn.config(state="disabled")
        self.status_label.config(text="Connecting...", foreground="orange")
        threading.Thread(target=self._connect_worker, args=(port,), daemon=True).start()

    def _connect_worker(self, port):
        try:
            self.link.connect(port)
        except serial.SerialException as e:
            self.rx_queue.put(("error", f"Connect failed: {e}"))
            self.rx_queue.put(("__conn__", False))
        else:
            self.rx_queue.put(("__conn__", True))

    def _set_connected(self, ok):
        self.connect_btn.config(state="normal",
                                text="Disconnect" if ok else "Connect")
        if ok:
            self.status_label.config(text="Connected", foreground="green")
        else:
            self.status_label.config(text="Disconnected", foreground="red")

    # --- Motor commands ----------------------------------------------------

    def _on_setting_changed(self):
        if not self.live_var.get():
            return
        # Debounce slider drags: send at most once per 60 ms.
        if self.pending_send is None:
            self.pending_send = self.after(60, self._debounced_send)

    def _debounced_send(self):
        self.pending_send = None
        self.send_current()

    def send_current(self):
        if not self.link.connected:
            self.log_line("error", "Not connected.")
            return
        self.cancel_ramp()
        self.link.send_move(self.left_panel.signed_percent(),
                            self.right_panel.signed_percent())

    def stop_all(self):
        self.cancel_ramp()
        self.left_panel.reset()
        self.right_panel.reset()
        if self.link.connected:
            self.link.send_stop()
        else:
            self.log_line("info", "STOP (not connected, sliders reset).")

    # --- Ramp (accel / decel) ----------------------------------------------

    RAMP_TICK_MS = 100

    def _ramp_duration(self):
        try:
            duration = float(self.ramp_time_var.get().replace(",", "."))
        except ValueError:
            self.log_line("error", f"Bad ramp time: {self.ramp_time_var.get()!r}")
            return None
        return max(0.1, min(120.0, duration))

    def start_accel(self):
        if not self.link.connected:
            self.log_line("error", "Not connected.")
            return
        target = (self.left_panel.signed_percent(), self.right_panel.signed_percent())
        if target == (0, 0):
            self.log_line("error", "Set speed is 0 — nothing to accelerate to.")
            return
        duration = self._ramp_duration()
        if duration is None:
            return
        self.log_line("info", f"Accel 0 -> L={target[0]} R={target[1]} over {duration:g} s")
        self._start_ramp((0, 0), target, duration, "Accel")

    def start_decel(self):
        if not self.link.connected:
            self.log_line("error", "Not connected.")
            return
        start = (self.link.left, self.link.right)
        if start == (0, 0):
            self.log_line("info", "Already stopped.")
            return
        duration = self._ramp_duration()
        if duration is None:
            return
        self.log_line("info", f"Decel L={start[0]} R={start[1]} -> 0 over {duration:g} s")
        self._start_ramp(start, (0, 0), duration, "Decel")

    def _start_ramp(self, start, end, duration, name):
        self.cancel_ramp()
        t0 = time.monotonic()
        self._ramp_tick(start, end, duration, name, t0)

    def _ramp_tick(self, start, end, duration, name, t0):
        self.ramp_job = None
        if not self.link.connected:
            self.ramp_status.config(text="idle")
            return
        progress = min(1.0, (time.monotonic() - t0) / duration)
        left = round(start[0] + (end[0] - start[0]) * progress)
        right = round(start[1] + (end[1] - start[1]) * progress)
        self.link.send_move(left, right)
        self.ramp_status.config(text=f"{name}: L={left} R={right}")

        if progress >= 1.0:
            self.ramp_status.config(text=f"{name} done")
            if end == (0, 0):
                self.link.send_stop()
                self.left_panel.reset()
                self.right_panel.reset()
            return
        self.ramp_job = self.after(self.RAMP_TICK_MS, self._ramp_tick,
                                   start, end, duration, name, t0)

    def cancel_ramp(self):
        if self.ramp_job is not None:
            self.after_cancel(self.ramp_job)
            self.ramp_job = None
            self.ramp_status.config(text="cancelled")

    # --- Log / event pump --------------------------------------------------

    def log_line(self, tag, text):
        prefix = {"tx": "> ", "rx": "< ", "error": "!! ", "info": "-- "}.get(tag, "")
        self.log.config(state="normal")
        self.log.insert("end", prefix + text + "\n", tag)
        self.log.see("end")
        # Keep log bounded.
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "100.0")
        self.log.config(state="disabled")

    def _poll_rx_queue(self):
        try:
            while True:
                tag, payload = self.rx_queue.get_nowait()
                if tag == "__conn__":
                    self._set_connected(payload)
                    if not payload:
                        self.link.disconnect()
                else:
                    self.log_line(tag, payload)
        except queue.Empty:
            pass
        self.after(50, self._poll_rx_queue)

    def _on_close(self):
        self.link.disconnect()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
