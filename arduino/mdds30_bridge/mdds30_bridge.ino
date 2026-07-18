/*
  Arduino Motor Bridge
  Cytron MDDS30 in PWM/DIR Independent Both Signed Magnitude mode

  PC -> USB Serial -> Arduino -> PWM/DIR -> MDDS30

  Command examples:
    M L=-30 R=30
    M L=0 R=0
    STOP
    PING
    STATUS
    TIMEOUT=300
*/

const int PWM_L = 5;   // Arduino PWM pin -> MDDS30 AN1
const int DIR_L = 7;   // Arduino digital pin -> MDDS30 IN1
const int PWM_R = 6;   // Arduino PWM pin -> MDDS30 AN2
const int DIR_R = 8;   // Arduino digital pin -> MDDS30 IN2

// Adjust these after physical direction test.
// HIGH or LOW is arbitrary from the robot point of view; choose whichever makes the wheel move forward.
const int LEFT_FORWARD_LEVEL  = HIGH;
const int RIGHT_FORWARD_LEVEL = HIGH;

int lastLeftPercent = 0;
int lastRightPercent = 0;

unsigned long lastCommandMs = 0;
unsigned long watchdogTimeoutMs = 300;

String inputLine = "";

void setMotor(int pwmPin, int dirPin, int forwardLevel, int percent) {
  percent = constrain(percent, -100, 100);

  if (percent == 0) {
    analogWrite(pwmPin, 0);
    return;
  }

  bool forward = (percent > 0);
  int directionLevel = forward ? forwardLevel : !forwardLevel;
  int pwm = map(abs(percent), 0, 100, 0, 255);

  digitalWrite(dirPin, directionLevel);
  analogWrite(pwmPin, pwm);
}

void applyMotors(int leftPercent, int rightPercent) {
  lastLeftPercent = constrain(leftPercent, -100, 100);
  lastRightPercent = constrain(rightPercent, -100, 100);

  setMotor(PWM_L, DIR_L, LEFT_FORWARD_LEVEL, lastLeftPercent);
  setMotor(PWM_R, DIR_R, RIGHT_FORWARD_LEVEL, lastRightPercent);
}

void stopMotors() {
  applyMotors(0, 0);
}

bool parseMoveCommand(const String &line, int &leftOut, int &rightOut) {
  int lIndex = line.indexOf("L=");
  int rIndex = line.indexOf("R=");

  if (lIndex < 0 || rIndex < 0) {
    return false;
  }

  leftOut = line.substring(lIndex + 2).toInt();
  rightOut = line.substring(rIndex + 2).toInt();

  leftOut = constrain(leftOut, -100, 100);
  rightOut = constrain(rightOut, -100, 100);
  return true;
}

void handleCommand(String line) {
  line.trim();
  line.toUpperCase();

  if (line.length() == 0) {
    return;
  }

  if (line == "PING") {
    lastCommandMs = millis();
    Serial.println("OK PONG");
    return;
  }

  if (line == "STOP") {
    stopMotors();
    lastCommandMs = millis();
    Serial.println("OK STOP");
    return;
  }

  if (line == "STATUS") {
    Serial.print("OK L=");
    Serial.print(lastLeftPercent);
    Serial.print(" R=");
    Serial.print(lastRightPercent);
    Serial.print(" TIMEOUT=");
    Serial.println(watchdogTimeoutMs);
    return;
  }

  if (line.startsWith("TIMEOUT=")) {
    long value = line.substring(8).toInt();
    if (value < 50 || value > 5000) {
      Serial.println("ERR timeout_out_of_range");
      return;
    }
    watchdogTimeoutMs = (unsigned long)value;
    lastCommandMs = millis();
    Serial.print("OK TIMEOUT=");
    Serial.println(watchdogTimeoutMs);
    return;
  }

  if (line.startsWith("M ") || line.startsWith("M\t")) {
    int left = 0;
    int right = 0;

    if (!parseMoveCommand(line, left, right)) {
      Serial.println("ERR bad_move_command");
      return;
    }

    applyMotors(left, right);
    lastCommandMs = millis();

    Serial.print("OK L=");
    Serial.print(lastLeftPercent);
    Serial.print(" R=");
    Serial.println(lastRightPercent);
    return;
  }

  Serial.println("ERR unknown_command");
}

void setup() {
  pinMode(PWM_L, OUTPUT);
  pinMode(DIR_L, OUTPUT);
  pinMode(PWM_R, OUTPUT);
  pinMode(DIR_R, OUTPUT);

  // Safe default before serial starts.
  analogWrite(PWM_L, 0);
  analogWrite(PWM_R, 0);
  digitalWrite(DIR_L, LEFT_FORWARD_LEVEL);
  digitalWrite(DIR_R, RIGHT_FORWARD_LEVEL);

  Serial.begin(115200);
  inputLine.reserve(64);

  lastCommandMs = millis();

  Serial.println("OK HERMES_MDDS30_BRIDGE_READY");
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n') {
      handleCommand(inputLine);
      inputLine = "";
    } else if (c != '\r') {
      if (inputLine.length() < 63) {
        inputLine += c;
      } else {
        inputLine = "";
        Serial.println("ERR line_too_long");
      }
    }
  }

  if (watchdogTimeoutMs > 0 && (millis() - lastCommandMs > watchdogTimeoutMs)) {
    if (lastLeftPercent != 0 || lastRightPercent != 0) {
      stopMotors();
      Serial.println("OK WATCHDOG_STOP");
    }
    lastCommandMs = millis();
  }
}
