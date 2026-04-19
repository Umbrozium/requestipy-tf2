import logging
import json
import os
import src.core_commands

__version__ = "1.0"

logger = logging.getLogger(__name__)

PLUGIN_CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'plugin_config.json'))

def load_volume() -> float:
    if os.path.exists(PLUGIN_CONFIG_PATH):
        try:
            with open(PLUGIN_CONFIG_PATH, 'r') as f:
                return float(json.load(f).get('volume', 0.2))
        except Exception as e:
            logger.error(f"Failed to load volume config: {e}")
    return 0.2

def save_volume(vol: float):
    config = {}
    if os.path.exists(PLUGIN_CONFIG_PATH):
        try:
            with open(PLUGIN_CONFIG_PATH, 'r') as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read plugin config for updating: {e}")
            
    config['volume'] = vol
    
    try:
        with open(PLUGIN_CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save volume config: {e}")

def cmd_volume(user, args):
    """Handles the !volume command to adjust playback level."""
    audio_player = src.core_commands._audio_player_instance
    if not audio_player:
        logger.error("Audio player instance not available for !volume command.")
        return

    if not args:
        current_vol = audio_player.get_volume()
        logger.info(f"Current volume is {current_vol * 100:.0f}%")
        return

    try:
        vol_str = args[0]
        vol_val = float(vol_str)
        
        if vol_val > 2.0: 
            vol_val = vol_val / 100.0

        audio_player.set_volume(vol_val)
        logger.info(f"User {user['name']} set volume to {vol_val * 100:.0f}%")
        
        save_volume(audio_player.get_volume())
        logger.info("New volume saved to plugin_config.json successfully.")
    except ValueError:
        logger.warning(f"Invalid volume argument provided by {user['name']}: {args[0]}")

def register(command_manager, event_bus):
    # Set the initial volume on startup
    audio_player = src.core_commands._audio_player_instance
    if audio_player:
        initial_vol = load_volume()
        audio_player.set_volume(initial_vol)
        logger.info(f"Plugin 'volume' initialized audio player volume to {initial_vol * 100:.0f}%")

    command_manager.register_command(
        "volume", 
        cmd_volume, 
        help_text="Sets the volume (e.g., !volume 0.15 or !volume 15)",
        aliases=["vol"],
        source="plugin_volume"
    )

def unregister(command_manager, event_bus):
    command_manager.unregister_command("volume")
