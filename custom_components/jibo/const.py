DOMAIN = "jibo"
PLATFORMS = ["binary_sensor"]

CONF_SERVER_URL = "server_url"
CONF_SERVER_MODE = "server_mode"
CONF_JIBO_IP = "jibo_ip"
CONF_INSTANCE_ID = "instance_id"
CONF_LINK_ID = "link_id"
CONF_COMMAND_SECRET = "command_secret"
CONF_JIBO_FRIENDLY_NAME = "jibo_friendly_name"
CONF_WEBHOOK_ID = "webhook_id"
CONF_HA_PORT = "ha_port"

MODE_5X1 = "5x1"
MODE_SELF_HOST_BEEFY = "self_host_beefy"
MODE_OPENJIBO_COM = "openjibo_com"
MODE_SELF_HOST_OPENJIBO = "self_host_openjibo"
BEEFY_MODES = frozenset({MODE_5X1, MODE_SELF_HOST_BEEFY})

OPENJIBO_COM_URL = "https://api.openjibo.com"
FIVE_X1_URL = "https://api.5x1.com"
BEACON_PORT = 8123
PAIR_PATH = "/api/homeassistant/pair"
PAIR_TIMEOUT_SECONDS = 90

WS_PATH = "/v1/homeassistant/ws"
NOTIFICATION_ID_PREFIX = "openjibo_pairing_"


def is_beefy_mode(mode: str | None) -> bool:
    return mode in BEEFY_MODES
