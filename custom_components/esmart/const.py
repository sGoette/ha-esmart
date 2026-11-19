"""Constants for the eSmart integration."""

DOMAIN = "esmart"
MANUFACTURER = "eSmart"

CONF_JID = "jid"
CONF_PASSWORD = "password"
CONF_SWITCHABLE = "switchable_nodes"  # options: node ids the user declared switchable
CONF_DOORS = "door_opening"  # options: user enabled the door locks (CMD/door pulse); default off

PLATFORMS = ["light", "cover", "climate", "sensor", "binary_sensor", "lock"]
