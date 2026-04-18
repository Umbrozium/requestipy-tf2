import json
import os
import logging
from typing import Dict, Any # Import Dict and Any

# Import jsonschema if available, otherwise handle gracefully
try:
    from jsonschema import validate
    from jsonschema.exceptions import ValidationError
    _jsonschema_available = True
except ImportError:
    _jsonschema_available = False
    validate = None
    ValidationError = None
    logging.warning("jsonschema library not found. Configuration validation will be skipped.")


DEFAULT_CONFIG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'config.json'))

# --- Define Configuration Schema ---
# Based on observed usage and config.json example
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "game_dir": {"type": "string", "description": "Path to the TF2 'tf' directory."},
        "log_file_name": {"type": "string", "default": "console.log", "description": "Name of the console log file."},
        "admin_user": {"type": "string", "description": "Case-sensitive in-game username of the admin."},
        "ignored_users": {
            "type": "array",
            "items": {"type": "string"},
            "default": [],
            "description": "List of usernames to ignore commands from."
        },
        "ignored_reversed": {"type": "boolean", "default": False, "description": "If true, only allow commands from ignored_users."},
        "log_level": {
            "type": "string",
            "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            "default": "INFO",
            "description": "Logging level for the application."
        },
        "output_device_substring": {
            "type": ["string", "null"], 
            "default": None,
            "description": "Substring to identify the virtual audio output device (e.g., 'CABLE Input'). Null/empty uses default."
        },
        # ADD THE VOLUME PROPERTY HERE:
        "volume": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 2.0,
            "default": 0.2,
            "description": "Audio playback volume multiplier (0.0 to 1.0 is standard)."
        },
        "team": {
            "type": "string",
            "enum": ["yes", "no"],
            "default": "no",
            "description": "If 'yes', only parses commands from team chat messages."
        }
    },
    "required": ["game_dir", "admin_user"],
    "additionalProperties": False 
}

# --- Custom Exception ---
class ConfigError(Exception):
    """Custom exception for configuration loading errors."""
    pass


def _apply_defaults(config_data: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    """Applies default values from the schema to the config data."""
    for key, prop_schema in schema.get("properties", {}).items():
        if "default" in prop_schema and key not in config_data:
            config_data[key] = prop_schema["default"]
            logging.debug(f"Applied default value for config key '{key}': {config_data[key]}")
    return config_data

def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Loads and validates configuration from a JSON file.

    Args:
        config_path: Path to the configuration file.

    Returns:
        A dictionary containing the validated configuration settings with defaults applied.

    Raises:
        ConfigError: If the file doesn't exist, is invalid JSON, or fails schema validation.
    """
    if not os.path.exists(config_path):
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Error decoding JSON configuration file {config_path}: {e}") from e
    except IOError as e:
        raise ConfigError(f"Error reading configuration file {config_path}: {e}") from e
    except Exception as e: # Catch unexpected errors during file read/load
        raise ConfigError(f"Unexpected error loading configuration file {config_path}: {e}") from e

    # --- Apply Defaults ---
    config_data = _apply_defaults(config_data, CONFIG_SCHEMA)

    # --- Validate Schema ---
    if _jsonschema_available and validate:
        try:
            validate(instance=config_data, schema=CONFIG_SCHEMA)
            logging.info(f"Configuration loaded and validated successfully from {config_path}")
        except ValidationError as e:
            # Provide a more helpful error message
            error_message = f"Configuration validation failed: {e.message} (path: {'/'.join(map(str, e.path))})"
            logging.error(error_message) # Log the specific validation error
            # Optionally log the full schema and data for debugging
            # logging.debug(f"Schema: {json.dumps(CONFIG_SCHEMA, indent=2)}")
            # logging.debug(f"Data: {json.dumps(config_data, indent=2)}")
            raise ConfigError(error_message) from e
        except Exception as e: # Catch unexpected validation errors
             raise ConfigError(f"Unexpected error during configuration validation: {e}") from e
    elif not _jsonschema_available:
         logging.warning(f"Configuration loaded from {config_path}, but schema validation skipped (jsonschema not installed).")
    # ---------------------

    return config_data

def save_config(config_data: Dict[str, Any], config_path: str = DEFAULT_CONFIG_PATH):
    """Saves configuration data to a JSON file.

    Args:
        config_data: The configuration dictionary to save.
        config_path: Path to the configuration file.

    Raises:
        ConfigError: If there's an error writing the file or serializing data.
    """
    # Optional: Validate before saving?
    # if _jsonschema_available and validate:
    #     try:
    #         validate(instance=config_data, schema=CONFIG_SCHEMA)
    #     except ValidationError as e:
    #         raise ConfigError(f"Configuration validation failed before saving: {e.message}") from e

    try:
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False) # Use indent=2 for readability
            logging.info(f"Configuration saved successfully to {config_path}")
    except IOError as e:
        raise ConfigError(f"Error writing configuration file {config_path}: {e}") from e
    except TypeError as e:
        raise ConfigError(f"Error serializing configuration data to JSON: {e}") from e
    except Exception as e: # Catch unexpected errors during save
        raise ConfigError(f"Unexpected error saving configuration file {config_path}: {e}") from e

# example usage (can be removed or kept for testing)
if __name__ == '__main__':
    # set up basic logging for testing this module directly
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

    # --- Create a dummy config for testing ---
    DUMMY_CONFIG_DIR = "temp_test_config"
    DUMMY_CONFIG_PATH = os.path.join(DUMMY_CONFIG_DIR, "test_config.json")
    if not os.path.exists(DUMMY_CONFIG_DIR): os.makedirs(DUMMY_CONFIG_DIR)

    # Valid config
    valid_data = {
        "game_dir": "/path/to/tf",
        "admin_user": "test_admin",
        "log_level": "DEBUG",
        "output_device_substring": "Cable Input"
        # ignored_users and ignored_reversed will use defaults
    }
    # Invalid config (missing required field)
    invalid_data_missing = {
         "game_dir": "/path/to/tf"
         # missing admin_user
    }
    # Invalid config (wrong type)
    invalid_data_type = {
        "game_dir": "/path/to/tf",
        "admin_user": "test_admin",
        "log_level": 123 # wrong type
    }
    # Invalid config (extra field)
    invalid_data_extra = {
        "game_dir": "/path/to/tf",
        "admin_user": "test_admin",
        "extra_field": "not allowed"
    }

    def run_test(data_to_save, test_name):
        print(f"\n--- Testing: {test_name} ---")
        try:
            save_config(data_to_save, DUMMY_CONFIG_PATH)
            print("Saved test config.")
            loaded_settings = load_config(DUMMY_CONFIG_PATH)
            print("Loaded config:", loaded_settings)
            print(f"Result: SUCCESS")
        except ConfigError as e:
            print(f"Caught expected ConfigError: {e}")
            print(f"Result: FAILED AS EXPECTED (if validation was intended to fail)")
        except Exception as e:
             print(f"Caught UNEXPECTED Exception: {e}")
             print(f"Result: UNEXPECTED FAILURE")
        finally:
             # Clean up dummy file
             if os.path.exists(DUMMY_CONFIG_PATH):
                 try: os.remove(DUMMY_CONFIG_PATH)
                 except Exception: pass

    # Run tests only if jsonschema is available for validation tests
    if _jsonschema_available:
        run_test(valid_data, "Valid Config")
        run_test(invalid_data_missing, "Invalid Config (Missing Required)")
        run_test(invalid_data_type, "Invalid Config (Wrong Type)")
        run_test(invalid_data_extra, "Invalid Config (Extra Property)")
    else:
        print("\n--- Skipping validation tests (jsonschema not installed) ---")
        # Test basic load/save without validation
        run_test(valid_data, "Valid Config (No Validation)")


    # Test file not found
    print("\n--- Testing: File Not Found ---")
    non_existent_path = os.path.join(DUMMY_CONFIG_DIR, "non_existent.json")
    try:
        load_config(non_existent_path)
    except ConfigError as e:
        print(f"Caught expected ConfigError: {e}")
        print(f"Result: SUCCESS")
    except Exception as e:
        print(f"Caught UNEXPECTED Exception: {e}")
        print(f"Result: UNEXPECTED FAILURE")

    # Clean up dummy directory
    import shutil
    if os.path.exists(DUMMY_CONFIG_DIR):
        try: shutil.rmtree(DUMMY_CONFIG_DIR)
        except Exception: pass