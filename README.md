# Cytron Motordriver Tester

Cross-platform desktop GUI for testing two brushed DC motors driven by a
**Cytron MDDS30 (SmartDriveDuo-30)** motor driver, with an **Arduino UNO**
acting as a USB-serial-to-PWM/DIR bridge.

```
Laptop / PC (this GUI)
      |  USB cable (USB-serial, 115200 baud, ASCII line protocol)
      v
Arduino UNO (bridge sketch, arduino/mdds30_bridge/)
      |  PWM + DIR logic signals
      v
Cytron MDDS30
      |  power stage
      v
2x brushed DC motor
```

The PC never drives the motors directly. It sends high-level speed commands;
the Arduino converts them to PWM/DIR signals and enforces a communication
watchdog — if commands stop arriving (app killed, cable unplugged), the
Arduino stops both motors on its own.

## Repository contents

| Path | Description |
|------|-------------|
| `motor_control.py` | Desktop GUI (Python, tkinter + pyserial). Runs on macOS, Windows, Linux. |
| `arduino/mdds30_bridge/mdds30_bridge.ino` | Arduino UNO bridge sketch. Upload with Arduino IDE. |

## Requirements (PC side)

- Python 3.8+ with tkinter
  - macOS / Windows: included in the python.org installer
  - Linux: `sudo apt install python3-tk`
- pyserial: `pip install pyserial`

Run:

```
python3 motor_control.py
```

## Hardware

- Cytron MDDS30 / SmartDriveDuo-30
- Arduino UNO (or compatible 5 V board)
- Two brushed DC motors (MDDS30 is **not** for brushless motors)
- Separate motor battery connected to the MDDS30 Vmotor terminals
  (Arduino USB power is *not* motor power)

## Wiring: Arduino UNO → MDDS30

Signal (logic) side:

| Arduino UNO | Type    | MDDS30 | Function                  |
|-------------|---------|--------|---------------------------|
| D5          | PWM     | AN1    | Left motor speed          |
| D7          | DIGITAL | IN1    | Left motor direction      |
| D6          | PWM     | AN2    | Right motor speed         |
| D8          | DIGITAL | IN2    | Right motor direction     |
| GND         | GND     | GND    | Common logic ground (required) |

```
Arduino UNO        MDDS30
-------------------------
D5  PWM      --->  AN1   left motor speed
D7  DIGITAL  --->  IN1   left motor direction

D6  PWM      --->  AN2   right motor speed
D8  DIGITAL  --->  IN2   right motor direction

GND          --->  GND   common ground
```

Power side (kept separate from logic):

```
Motor battery +  --->  Vmotor +
Motor battery -  --->  Vmotor -

Left motor       --->  MLA / MLB
Right motor      --->  MRA / MRB
```

Do **not** connect the motor battery to the Arduino 5 V pin, and do not
power motors from the Arduino.

## MDDS30 DIP switch configuration

Mode: *Microcontroller PWM input, Independent Both, Signed Magnitude,
Linear response.*

| Switch | Position | Meaning |
|--------|----------|---------|
| SW1    | ON       | PWM input mode (with SW2) |
| SW2    | OFF      | PWM input mode (with SW1) |
| SW3    | ON       | Independent Both motors (with SW4) |
| SW4    | ON       | Independent Both motors (with SW3) |
| SW5    | OFF      | Linear response |
| SW6    | ON       | Signed Magnitude mode |

SW7/SW8 select the battery-monitor type, not the control mode:

| Battery monitor | SW7 | SW8 |
|-----------------|-----|-----|
| LiPo            | OFF | OFF |
| NiMH            | OFF | ON  |
| SLA (lead-acid) | ON  | OFF |
| Off             | ON  | ON  |

**Important:** change DIP switches only with power off. The MDDS30 reads
the input mode at startup/reset — after changing switches, power-cycle the
driver or press its RESET button. The manual also requires a valid stop
signal to be present at power-up; in this mode, stop means **0 % PWM duty
cycle** on AN1 and AN2 (the bridge sketch outputs this from boot).

## Recommended power-up order

1. Upload the bridge sketch to the Arduino.
2. Connect the GUI (or Serial Monitor) and confirm the Arduino reports
   `OK HERMES_MDDS30_BRIDGE_READY` with motors stopped.
3. Power the MDDS30 motor supply.
4. If the MDDS30 reports an input error, send `STOP` and press its RESET
   button.

First tests: **wheels off the ground.**

## Serial protocol (PC → Arduino)

115200 baud, 8N1, ASCII lines terminated by `\n`.

| Command | Effect | Reply |
|---------|--------|-------|
| `M L=<l> R=<r>` | Set both motors. `<l>`, `<r>` are integers −100…100: 0 = stop, positive = forward, negative = reverse, magnitude = % PWM. | `OK L=<l> R=<r>` |
| `STOP` | Immediately stop both motors. | `OK STOP` |
| `PING` | Liveness check. | `OK PONG` |
| `STATUS` | Report last commanded values and timeout. | `OK L=<l> R=<r> TIMEOUT=<ms>` |
| `TIMEOUT=<ms>` | Set watchdog timeout, 50–5000 ms (default 300). | `OK TIMEOUT=<ms>` |

Rejected commands answer `ERR <reason>`.

**Watchdog:** if no valid command arrives within the timeout, the Arduino
stops both motors and prints `OK WATCHDOG_STOP`. The GUI therefore resends
the active movement command every 100 ms while any motor is running.

## GUI usage

1. Connect the Arduino via USB, click **Refresh**, pick the port
   (macOS: `/dev/cu.usbmodem*`, Linux: `/dev/ttyACM0`, Windows: `COM*`).
2. **Connect** — the app waits ~2 s for the Arduino auto-reset, then sends
   `STOP` and `PING`.
3. Per motor: choose **Forward/Reverse**, set velocity with the slider or
   preset buttons (0/25/50/75/100).
4. **Live update** on (default): every change is sent immediately.
   Off: press **Send** to apply.
5. **STOP** button or **Space** key: zeroes both sliders and sends `STOP`.
6. **Ramp**: set the time in seconds, then
   - **Accelerate 0 → set speed** — linear ramp from zero to the currently
     set direction/velocity of both motors over that time;
   - **Decelerate → 0** — linear ramp from the current commanded speed down
     to zero, then `STOP`.

   A ramp cancels if you press STOP, press Send, or move a slider while
   live update is on.

The serial log shows sent (`>`) and received (`<`) lines.

## Direction calibration

"Forward" depends on motor wiring and mounting. If a motor runs the wrong
way for a positive command, either flip `LEFT_FORWARD_LEVEL` /
`RIGHT_FORWARD_LEVEL` in the sketch, or power off the driver and swap that
motor's two output wires. Never swap motor wires while the driver is
powered.

## Safety notes

- MDDS30 does not protect against reversed motor-supply polarity —
  double-check Vmotor + / − before power-up.
- Fuse or breaker in the motor battery line is strongly recommended.
- Arduino GND and MDDS30 GND must be connected together.
- First tests with wheels off the ground.
