# OpenJibo
This Home Assistant integration allows you to connect your Jibo device, enabling control over:
- Lights
- Thermostats
- (Likely more in the future)
## Requirements:
- Jibo Robot (Must be pointed at an OpenJibo Server)
- Home Assistant (With HACS installed)
- Connection to Internet
- Access to an OpenJibo Server (self-hosted is supported)
## Setup:
1. Go to your Devices menu and click "Add Integration"
2. Select/Search OpenJibo in the new menu
3. Enter the URL of your OpenJibo Server (e.g. https://api.5x1.com:80)
4. Set an Integration Name your Jibo (it doesn't matter what you pick)
5. Enter the IP of your Robot (optional, requires HA to be on the same network as Jibo)
6. Go to the OpenJibo portal (<SERVER_URL>/portal)
7. On the portal, sign in via the code given by your Jibo
8. Go to your Home Assistant Notifications and copy your pairing code
9. Paste your pairing code into the OpenJibo portal
Your robot should now work with Home Assistant. If it doesn't visit our [website](https://jiborevived.com) for support
## What This Does:
This integration will add support for Jibo to control lights and thermostats within your Home. It also exposes (if you've entered his IP) a entity that tells you whether or not he is online. OpenJibo also gives you the say service which (if you've entered his IP) allows you to make him say things, via his built-in TTS service.
