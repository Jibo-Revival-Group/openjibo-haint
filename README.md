# OpenJibo
This Home Assistant integration lets your Jibo control:
- Lights
- Thermostats
- (Likely more in the future)

## Requirements
- Jibo robot
- Home Assistant with HACS installed
- For 5x1, OpenJibo.com, or a self-hosted server: internet (or a reachable server)
- For 5x1 and Self-Host BEefy: Home Assistant and the Jibo on the same network

## Setup
1. Go to Devices and click Add Integration
2. Search for OpenJibo
3. Choose **Which Server is Used?**

### 5x1
Uses BEefy at `api.5x1.com`. Enter the Jibo's IP address. The robot shows that Home Assistant machine's IP and asks Yes or No. On Yes it saves a shared password and points its conversation server at `api.5x1.com`. After that, BEefy tells the robot what to send when you say things like "turn on the lights," and the robot calls Home Assistant. If the Jibo's IP changes, it tells Home Assistant the new address and proves it with the password.

### Self-Host BEefy
Same pairing as 5x1, but the robot keeps whatever conversation server it already uses. Point the robot at your BEefy server before setup.

### OpenJibo.com
Uses `https://api.openjibo.com`. Enter a name and, if you want, the robot's IP (for the online sensor and `jibo.say`). A pairing code appears as a Home Assistant notification. Sign in at the OpenJibo portal with the code your Jibo speaks, then paste the notification code into the portal.

### Self-Host OpenJibo
Same as OpenJibo.com, except you enter your own server URL (for example `http://192.168.1.10:24605`). Finish pairing in that server's portal (`<SERVER_URL>/portal`).

### Phoenix
Not supported.

## What this does
Jibo can control lights and thermostats. If you entered his IP, the integration adds an entity that shows whether he is online, and the `jibo.say` service can make him speak through his built-in TTS service.

If something does not work, visit [jiborevived.com](https://jiborevived.com) for support.

## Light command troubleshooting

Assign the OpenJibo device and room lights to the same Home Assistant Area. Light entities inherit their device's Area unless they have their own Area assigned.

With the updated BEefy server and integration installed, Jibo waits up to three seconds for a light-command result. A successful reply confirms that Home Assistant completed the service call; it does not verify the physical light's state. BEefy and 5x1 always return commands through the requesting robot, which authenticates to Home Assistant using its saved Yes/No pairing. They do not use portal pairing codes or the legacy Home Assistant WebSocket connection. Portal pairing remains available only for older servers such as OpenJibo.com.

Enable debug logging for OpenJibo and repeat the command. Match the `requestId` in the integration's command receipt, target Area, and result logs with the server's dispatch and result logs. Missing Areas, empty rooms, authentication failures, and service errors now return failures. A timeout means confirmation did not arrive; the action may still have executed, so check the light before repeating it.

After updating both components, restart the integration and server, then test one room-light command and one named-light command. Each should produce one service call and one correlated result. If Jibo requests pairing, use the pairing flow for your selected server above.

Run the mocked receiver and reconnect tests from this repository with:

```sh
python3 -m unittest discover -s tests -v
```
# Jibo telemetry

**Charging State** reports Charging, Not Charging, or Not Plugged In.
**Microphone RMS** reports the SDK's raw `db_rms` waveform level in dB; it is
not calibrated environmental sound-pressure level (dB SPL). **Head Touch**
reports whether any head pad is touched. Audio and touch share a separate
one-second refresh, while the other readings refresh every 30 seconds.
**Sleeping** uses the same one-second refresh and reports on during nighttime
sleep or a daytime nap, and off when awake or running another skill. An
uninitialized sleep state or lost connection makes it unavailable.
The **Go to Sleep** button invokes Jibo's normal sleep behavior. Privacy mode,
active camera streaming, or an uninterruptible skill can refuse the command.

The **Speak** action (`jibo.say`) includes a picker for one or multiple Jibo
devices. Leave Robots empty to speak on all configured robots. Existing YAML
using a robot name in `robot` still works.

Native BE/BEacon pairing adds a **Battery** percentage sensor to the existing
Jibo device, alongside **Plugged in**, **Battery Temp**, **Main board Temp**,
**CPU Temp**, **System Voltage**, **Fan Speed**, **Speaker Volume**, and
**Hatch State**. Temperatures use °C, voltage uses V, and fan speed and speaker
volume use percentages. Hatch State shows open/closed; Plugged in shows power
connection status. All readings share one refresh every 30 seconds using the pairing credentials and
follows robot IP updates. Missing readings or connection failures mark the
sensor unavailable. Update BEam as well as this integration to enable it.

# Jibo camera streaming

Native BE/BEacon pairing provides a Camera entity and **Start Camera Stream**,
**Stop Camera Stream**, and **Toggle Camera Stream** buttons on the existing
Jibo device. HA must reach Jibo locally on port 8123 and have its ffmpeg
integration available. Viewing the Camera does not start capture.

Camera streaming is enabled for on-robot testing without a validation record.
See `BEam/docs/camera-streaming.md` in the BEcosystem workspace for native
transport details and testing steps. Streaming performance still needs to be
measured on a real Jibo. Cloud-only OpenJibo pairings do not expose these local camera entities.
