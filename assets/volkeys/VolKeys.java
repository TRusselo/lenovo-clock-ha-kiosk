package ks;

import android.hardware.input.InputManager;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;
import java.io.File;
import java.io.RandomAccessFile;
import java.lang.reflect.Method;

/**
 * Lenovo Smart Clock: turn the volume button GPIOs into volume key presses.
 * From the Smart Display tool's helper, with the pins as arguments.
 *
 * The buttons are GPIOs read by Android Things' sparrow.oem, which hands them to a Google listener
 * that doesn't run on the converted clock, so they did nothing. On the clock, VOLUME UP is GPIO 429
 * and VOLUME DOWN is GPIO 576 (the PMIC's key pin), both 1 while pressed; the mic switch is GPIO 410
 * and isn't touched. This watches the two pins and injects KEYCODE_VOLUME_UP and KEYCODE_VOLUME_DOWN
 * through InputManager, the same path as "input keyevent", in one long-running process. Run as root
 * (the Magisk module's service.sh):
 *   CLASSPATH=ks-volkeys.jar app_process /system/bin ks.VolKeys UP_VALUE_FILE DOWN_VALUE_FILE
 */
public final class VolKeys {
    private static final String UP_PIN = "/sys/class/gpio/gpio429/value";   // 1 while pressed
    private static final String DOWN_PIN = "/sys/class/gpio/gpio576/value"; // 1 while pressed
    private static final int POLL_MS = 30;
    // Music volume has 100 steps here, so one key is 1%; 5 per press = 5%,
    // matching Kiosk Satellite's volume step.
    private static final int KEYS_PER_PRESS = 5;
    private static final int REPEAT_DELAY_MS = 500;
    private static final int REPEAT_EVERY_MS = 300;

    private static InputManager inputManager;
    private static Method injectInputEvent;

    public static void main(String[] args) throws Exception {
        String upPin = args.length > 0 ? args[0] : UP_PIN;
        String downPin = args.length > 1 ? args[1] : DOWN_PIN;
        inputManager = (InputManager) InputManager.class.getMethod("getInstance").invoke(null);
        injectInputEvent = InputManager.class.getMethod("injectInputEvent", InputEvent.class, int.class);
        // The pins may be exported a moment after boot: wait for them rather than exit.
        while (!new File(upPin).exists() || !new File(downPin).exists()) {
            SystemClock.sleep(1000);
        }
        System.out.println("ks-volkeys: watching " + upPin + " and " + downPin);

        RandomAccessFile up = new RandomAccessFile(upPin, "r");
        RandomAccessFile down = new RandomAccessFile(downPin, "r");
        int held = 0;
        long heldSince = 0;
        long lastRepeat = 0;
        while (true) {
            int key = 0;
            if (pressed(up)) {
                key = KeyEvent.KEYCODE_VOLUME_UP;
            } else if (pressed(down)) {
                key = KeyEvent.KEYCODE_VOLUME_DOWN;
            }
            long now = SystemClock.uptimeMillis();
            if (key != 0 && key != held) {
                press(key);
                heldSince = now;
                lastRepeat = now;
            } else if (key != 0 && now - heldSince >= REPEAT_DELAY_MS && now - lastRepeat >= REPEAT_EVERY_MS) {
                press(key);
                lastRepeat = now;
            }
            held = key;
            SystemClock.sleep(POLL_MS);
        }
    }

    private static boolean pressed(RandomAccessFile pin) throws Exception {
        pin.seek(0);
        return pin.read() == '1';
    }

    private static void press(int key) throws Exception {
        for (int i = 0; i < KEYS_PER_PRESS; i++) {
            long t = SystemClock.uptimeMillis();
            inject(new KeyEvent(t, t, KeyEvent.ACTION_DOWN, key, 0, 0,
                    KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD));
            inject(new KeyEvent(t, t, KeyEvent.ACTION_UP, key, 0, 0,
                    KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD));
        }
    }

    private static void inject(KeyEvent event) throws Exception {
        // 0 = InputManager.INJECT_INPUT_EVENT_MODE_ASYNC
        injectInputEvent.invoke(inputManager, event, 0);
    }
}
