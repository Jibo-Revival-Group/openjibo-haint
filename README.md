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
