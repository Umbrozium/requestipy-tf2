import logging
import json
import os

logger = logging.getLogger(__name__)

PLUGIN_CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'plugin_config.json'))

def load_team_config() -> str:
    if os.path.exists(PLUGIN_CONFIG_PATH):
        try:
            with open(PLUGIN_CONFIG_PATH, 'r') as f:
                return str(json.load(f).get('team', 'no')).lower()
        except Exception as e:
            logger.error(f"Failed to load team config: {e}")
    return 'no'

def save_team_config(team_val: str):
    config = {}
    if os.path.exists(PLUGIN_CONFIG_PATH):
        try:
            with open(PLUGIN_CONFIG_PATH, 'r') as f:
                config = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read plugin config for updating: {e}")
            
    config['team'] = team_val
    
    try:
        with open(PLUGIN_CONFIG_PATH, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save team config: {e}")

def team_chat_filter(user, command_name, args) -> bool:
    """Filter that blocks commands if team mode is on and the message isn't team chat."""
    is_team_only = load_team_config() == "yes"
    
    # Check if any of the extracted tags contain the word "TEAM"
    user_tags = user.get('tags')
    is_team_chat = user_tags and "TEAM" in user_tags
    
    if is_team_only and not is_team_chat:
        logger.debug(f"Ignored command '!{command_name}' from {user.get('name')}: 'team' config is 'yes' but message lacks TEAM tag.")
        return False
        
    return True

def cmd_team(user, args):
    """Handles the !team command to toggle team-only mode."""
    if not args:
        current_val = load_team_config()
        logger.info(f"Current team mode is: {current_val}")
        return

    val = args[0].lower()
    if val in ["yes", "no"]:
        save_team_config(val)
        logger.info(f"User {user['name']} set team mode to {val}")
    else:
        logger.warning(f"Invalid team mode argument provided by {user['name']}: {args[0]}. Use 'yes' or 'no'.")

def register(command_manager, event_bus):
    # Add the filter to the command manager
    command_manager.add_command_filter(team_chat_filter)
    logger.info("Plugin 'teammode' initialized and team chat filter added.")

    command_manager.register_command(
        "team", 
        cmd_team, 
        help_text="Toggles team-only command parsing (e.g., !team yes or !team no)",
        admin_only=True,
        source="plugin_teammode"
    )

def unregister(command_manager, event_bus):
    command_manager.remove_command_filter(team_chat_filter)
    command_manager.unregister_command("team")
    logger.info("Plugin 'teammode' unregistered and team chat filter removed.")