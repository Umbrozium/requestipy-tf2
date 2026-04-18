import logging
from src.config import load_config, save_config
import src.core_commands

logger = logging.getLogger(__name__)

def cmd_volume(user, args):
    """Handles the !volume command to adjust playback level."""
    audio_player = src.core_commands._audio_player_instance
    if not audio_player:
        logger.error("Audio player instance not available for !volume command.")
        return

    if not args:
        # If they just type !volume, tell them the current volume
        current_vol = audio_player.get_volume()
        logger.info(f"Current volume is {current_vol * 100:.0f}%")
        # Note: If your framework has a way to send chat messages back to the server, 
        # you would trigger that here!
        return

    try:
        # Parse the input
        vol_str = args[0]
        vol_val = float(vol_str)
        
        # User-friendly check: if they type "15", they probably mean 15% (0.15)
        # If they type "0.15", they also mean 15%
        if vol_val > 2.0: 
            vol_val = vol_val / 100.0

        # Update the live audio player
        audio_player.set_volume(vol_val)
        logger.info(f"User {user['name']} set volume to {vol_val * 100:.0f}%")
        
        # Save the new volume to config.json so it persists after restarts!
        try:
            config = load_config()
            config['volume'] = audio_player.get_volume()
            save_config(config)
            logger.info("New volume saved to config.json successfully.")
        except Exception as e:
            logger.error(f"Failed to save volume to config: {e}")

    except ValueError:
        logger.warning(f"Invalid volume argument provided by {user['name']}: {args[0]}")

def register(command_manager, event_bus):
    command_manager.register_command(
        "volume", 
        cmd_volume, 
        help_text="Sets the volume (e.g., !volume 0.15 or !volume 15)",
        aliases=["vol"],
        source="plugin_volume"
    )

def unregister(command_manager, event_bus):
    command_manager.unregister_command("volume")
