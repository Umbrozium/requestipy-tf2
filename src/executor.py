import logging
import threading
import time # Import time
from typing import Dict, List, Any, Optional, Tuple

# assuming eventbus and commandmanager are accessible
from src.event_bus import EventBus
from src.command_manager import CommandManager, Command
from src.log_reader import EVENT_COMMAND_DETECTED # import event name

logger = logging.getLogger(__name__)

# Define a threshold for duplicate command detection (in seconds)
DUPLICATE_COMMAND_THRESHOLD = 0.5

# Define rate limit for non-admin users (in seconds)
NON_ADMIN_RATE_LIMIT_SECONDS = 30.0

class Executor:
    """listens for command events and executes them using commandmanager."""

    def __init__(self, config: Dict[str, Any], command_manager: CommandManager, event_bus: EventBus):
        self._config = config # store config
        self._command_manager = command_manager
        self._event_bus = event_bus
        # --- State for duplicate command detection ---
        self._last_event_time: float = 0.0
        self._last_event_details: Optional[Tuple[Optional[str], str, Tuple[str, ...]]] = None
        self._event_lock = threading.Lock() # Lock for accessing last event state
        # --------------------------------------------
        # --- State for rate limiting ---
        self._user_last_command_time: Dict[str, float] = {} # Key: username, Value: timestamp
        self._subscribe_to_events()
        logger.info("Executor initialized and subscribed to events.")

    def _subscribe_to_events(self):
        """subscribes the command execution handler to the relevant event."""
        self._event_bus.subscribe(EVENT_COMMAND_DETECTED, self.handle_command_event)
        logger.debug(f"executor subscribed to '{EVENT_COMMAND_DETECTED}' event.")

    def handle_command_event(self, user: Dict[str, Any], command: str, args: List[str]):
        """handles the command detected event."""
        current_time = time.time()
        command_name = command.lstrip('!') # command name without prefix
        user_name = user.get('name') # Get user name safely
        args_tuple = tuple(args) # Convert args list to tuple for comparison

        # Create details tuple for the current event
        current_details = (user_name, command_name, args_tuple)

        # --- Duplicate Check ---
        with self._event_lock: # Protect access to shared state
            time_diff = current_time - self._last_event_time
            is_duplicate = (current_details == self._last_event_details and
                            time_diff < DUPLICATE_COMMAND_THRESHOLD)

            if is_duplicate:
                logger.warning(f"Duplicate command detected within {time_diff:.2f}s. Ignoring: User='{user_name}', Command='!{command_name}', Args={args}")
                return # Ignore the likely duplicate event

            # Update last event details if not a duplicate
            self._last_event_time = current_time
            self._last_event_details = current_details
        # --- End Duplicate Check ---

        # --- Rate Limiting Check (Non-Admins) ---
        admin_user = self._config.get("admin_user")
        is_admin = admin_user and user_name and user_name.strip() == admin_user.strip()

        if not is_admin:
            # Use username for rate limiting if user_name is valid
            if user_name:
                last_time = self._user_last_command_time.get(user_name)
                if last_time:
                    elapsed = current_time - last_time
                    if elapsed < NON_ADMIN_RATE_LIMIT_SECONDS:
                        logger.warning(f"Rate limit exceeded for user '{user_name}'. Command '!{command_name}' ignored. Time left: {NON_ADMIN_RATE_LIMIT_SECONDS - elapsed:.1f}s")
                        return # Ignore command due to rate limit

                # If rate limit passed or first command for this user, update timestamp
                logger.debug(f"Updating last command time for non-admin user '{user_name}'")
                self._user_last_command_time[user_name] = current_time
            else:
                logger.warning(f"Cannot apply rate limit: Username not found in user data: {user}. Allowing command.")
        # --- End Rate Limiting Check ---


        # log the raw user dict received (after duplicate check)
        logger.debug(f"Executor processing command event: User Dict={user}, Command={command}, Args={args}")

        cmd_obj: Command | None = self._command_manager.get_command(command_name)

        if cmd_obj:
            # --- Global Command Filters ---
            for filter_func in self._command_manager.get_command_filters():
                try:
                    if not filter_func(user, command_name, args):
                        logger.debug(f"Command '!{command_name}' from '{user_name}' blocked by filter {filter_func.__name__}.")
                        return
                except Exception as e:
                    logger.error(f"Error in command filter {filter_func.__name__}: {e}", exc_info=True)
            # ------------------------------

            # --- admin check ---
            # is_admin check already performed above for rate limiting
            logger.debug(f"Admin check for command execution: is_admin={is_admin}")

            if cmd_obj.admin_only and not is_admin:
                logger.warning(f"Non-admin user '{user_name}' attempted to run admin command '!{command_name}'. Ignoring.")
                # optionally notify user they lack permission
                return # stop processing if not admin for admin-only command

            # --- execute command (if checks pass) ---
            # execute the command (command object handles enabled checks internally)
            try:
                # run in a separate thread to prevent blocking the event handler
                cmd_thread = threading.Thread(target=cmd_obj.execute, args=(user, args), daemon=True)
                cmd_thread.start()
                # cmd_obj.execute(user, args) # synchronous execution (can block)
            except Exception as e:
                logger.error(f"unexpected error trying to start execution thread for command !{cmd_obj.name}: {e}", exc_info=True)
        else:
            logger.warning(f"command '{command_name}' requested by {user_name} not found.")
            # optionally publish an "unknown_command" event or notify the user

    def shutdown(self):
        """unsubscribes from events."""
        logger.info("executor shutting down...")
        # unsubscribe might be useful if dynamically reloading components
        # self._event_bus.unsubscribe(EVENT_COMMAND_DETECTED, self.handle_command_event)
        # logger.debug(f"executor unsubscribed from '{EVENT_COMMAND_DETECTED}' event.")


# example usage (can be removed or kept for testing)
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

    # setup mock components
    test_bus = EventBus()
    # Use a real config dict for testing admin check
    test_config = {"admin_user": "alice"}
    test_cmd_manager = CommandManager(test_bus)

    def mock_play(user, args):
        print(f"--- MOCK PLAY --- User: {user['name']}, Args: {args}")
        import time
        time.sleep(0.2) # Simulate work
        print(f"--- MOCK PLAY DONE ---")

    def mock_help(user, args):
        print(f"--- MOCK HELP --- User: {user['name']}, Args: {args}")

    test_cmd_manager.register_command("play", mock_play, aliases=["p"], admin_only=True) # Make play admin only
    test_cmd_manager.register_command("help", mock_help)

    # Initialize Executor with config
    executor = Executor(test_config, test_cmd_manager, test_bus)

    print("\nPublishing command events...")
    dummy_user1 = {"name": "alice", "tags": None} # Admin
    dummy_user2 = {"name": "bob", "tags": "*DEAD*"} # Not Admin

    # Test admin command by admin
    test_bus.publish(EVENT_COMMAND_DETECTED, user=dummy_user1, command="!play", args=["song", "title"])
    time.sleep(0.1) # Short delay

    # Test admin command by non-admin (should be ignored)
    test_bus.publish(EVENT_COMMAND_DETECTED, user=dummy_user2, command="!play", args=["another", "song"])
    time.sleep(0.1)

    # Test non-admin command
    test_bus.publish(EVENT_COMMAND_DETECTED, user=dummy_user2, command="!help", args=[])
    time.sleep(0.1)

    # Test duplicate command quickly (should be ignored)
    print("\nTesting duplicate command...")
    test_bus.publish(EVENT_COMMAND_DETECTED, user=dummy_user1, command="!help", args=["test"])
    test_bus.publish(EVENT_COMMAND_DETECTED, user=dummy_user1, command="!help", args=["test"]) # Duplicate
    time.sleep(0.1)

    # Test same command after threshold (should execute)
    print("\nTesting same command after threshold...")
    time.sleep(DUPLICATE_COMMAND_THRESHOLD + 0.1)
    test_bus.publish(EVENT_COMMAND_DETECTED, user=dummy_user1, command="!help", args=["test"])


    print("\nWaiting for commands to finish (due to threading)...")
    # In a real app, the main loop would keep running. Here we just wait a bit.
    time.sleep(1)

    print("\nExecutor test finished.")
    # executor.shutdown() # Test shutdown if needed