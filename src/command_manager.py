import logging
from typing import Callable, Dict, List, Optional, Any

# assuming eventbus is accessible
from src.event_bus import EventBus

logger = logging.getLogger(__name__)

# type alias for command functions
# command function signature: func(user: dict, args: list[str], **kwargs) -> none
# user dict example: {"name": "playername", "tags": "*dead*"}
CommandCallable = Callable[[Dict[str, Any], List[str]], None]

class Command:
    """represents a registered command."""
    def __init__(self, name: str, func: CommandCallable, help_text: str = "", aliases: Optional[List[str]] = None, admin_only: bool = False, source: str = "core"):
        self.name = name.lower() # store command names in lowercase
        self.func = func
        self.help_text = help_text
        self.aliases = [alias.lower() for alias in aliases] if aliases else []
        self.admin_only = admin_only
        self.source = source # e.g., 'core' or plugin name
        self.enabled = True # commands are enabled by default

    def execute(self, user: Dict[str, Any], args: List[str]):
        """executes the command's function."""
        if not self.enabled:
            logger.warning(f"attempted to execute disabled command: !{self.name}")
            # optionally notify user or event bus
            return

        # todo: add admin check logic here if needed, potentially using config
        # if self.admin_only and not is_admin(user):
        #     logger.warning(f"user {user['name']} attempted to run admin command !{self.name}")
        #     # notify user?
        #     return

        try:
            logger.info(f"executing command !{self.name} for user {user['name']} with args: {args}")
            self.func(user, args)
        except Exception as e:
            logger.error(f"error executing command !{self.name}: {e}", exc_info=True)
            # optionally notify user or event bus about the error

    def __str__(self):
        return f"Command(name='{self.name}', aliases={self.aliases}, source='{self.source}', enabled={self.enabled})"


class CommandManager:
    """manages registration and lookup of commands."""

    def __init__(self, event_bus: EventBus):
        self._commands: Dict[str, Command] = {} # maps name/alias to command object
        self._event_bus = event_bus # may be used for events like command_registered
        self._command_filters: List[Callable[[Dict[str, Any], str, List[str]], bool]] = []
        logger.info("CommandManager initialized.")

    def add_command_filter(self, filter_func: Callable[[Dict[str, Any], str, List[str]], bool]):
        """adds a global filter function that runs before a command is executed.
        the filter should return True to allow execution, or False to block it."""
        if filter_func not in self._command_filters:
            self._command_filters.append(filter_func)
            logger.debug(f"Command filter {filter_func.__name__} added.")

    def remove_command_filter(self, filter_func: Callable[[Dict[str, Any], str, List[str]], bool]):
        """removes a global command filter."""
        if filter_func in self._command_filters:
            self._command_filters.remove(filter_func)
            logger.debug(f"Command filter {filter_func.__name__} removed.")

    def get_command_filters(self) -> List[Callable[[Dict[str, Any], str, List[str]], bool]]:
        """returns the list of active command filters."""
        return self._command_filters.copy()

    def register_command(self, name: str, func: CommandCallable, help_text: str = "", aliases: Optional[List[str]] = None, admin_only: bool = False, source: str = "core") -> bool:
        """registers a new command."""
        command_name = name.lower()
        if command_name in self._commands:
            logger.error(f"command registration failed: name '{command_name}' already registered by {self._commands[command_name].source}.")
            return False

        # check aliases for conflicts
        if aliases:
            for alias in aliases:
                alias_lower = alias.lower()
                if alias_lower in self._commands:
                     logger.error(f"command registration failed: alias '{alias_lower}' for command '{command_name}' already registered by {self._commands[alias_lower].source}.")
                     return False

        # create and store command
        command = Command(name=command_name, func=func, help_text=help_text, aliases=aliases, admin_only=admin_only, source=source)
        self._commands[command_name] = command
        if aliases:
            for alias in command.aliases:
                self._commands[alias] = command # map aliases directly to the command object

        logger.info(f"command registered: !{command_name} (aliases: {command.aliases}, source: {source})")
        # optionally publish an event
        # self._event_bus.publish("command_registered", command=command)
        return True

    def unregister_command(self, name: str) -> bool:
        """unregisters a command and its aliases."""
        command_name = name.lower()
        command = self._commands.get(command_name)

        if not command or command.name != command_name: # ensure we are removing the main name, not an alias
             logger.warning(f"command unregistration failed: command '{command_name}' not found or '{name}' is an alias.")
             return False

        # remove main name
        del self._commands[command_name]

        # remove aliases
        if command.aliases:
            for alias in command.aliases:
                if alias in self._commands and self._commands[alias] == command:
                    del self._commands[alias]

        logger.info(f"command unregistered: !{command_name} (source: {command.source})")
        # optionally publish an event
        # self._event_bus.publish("command_unregistered", command_name=command_name, source=command.source)
        return True

    def get_command(self, name: str) -> Optional[Command]:
        """finds a command by its name or alias."""
        logger.debug(f"looking up command/alias for raw input: '{name}'") # log raw input
        name_lower = name.lower() # convert to lowercase
        logger.debug(f"attempting lookup with lowercased name: '{name_lower}'")
        command = self._commands.get(name_lower)
        logger.debug(f"lookup result: {'found' if command else 'not found'}") # log result
        return command

    def get_all_commands(self) -> List[Command]:
        """returns a list of unique registered command objects."""
        # use a set to get unique command objects, as aliases point to the same object
        return list({cmd for cmd in self._commands.values()})

    def enable_command(self, name: str):
        """enables a command."""
        command = self.get_command(name)
        if command:
            command.enabled = True
            logger.info(f"command !{command.name} enabled.")
        else:
            logger.warning(f"could not enable command: !{name} not found.")

    def disable_command(self, name: str):
        """disables a command."""
        command = self.get_command(name)
        if command:
            command.enabled = False
            logger.info(f"command !{command.name} disabled.")
        else:
            logger.warning(f"could not disable command: !{name} not found.")


# example usage (can be removed or kept for testing)
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    test_bus = EventBus() # dummy event bus for testing
    manager = CommandManager(test_bus)

    def cmd_hello(user, args):
        print(f"hello {user['name']}! args: {args}")

    def cmd_test(user, args):
        print(f"test command executed by {user['name']}. args: {args}")

    print("\nregistering commands...")
    manager.register_command("hello", cmd_hello, help_text="says hello.", aliases=["hi", "hey"])
    manager.register_command("test", cmd_test, help_text="a test command.", source="plugin_a")
    manager.register_command("hi", cmd_hello) # test duplicate alias registration (should fail)
    manager.register_command("fail", cmd_hello, aliases=["test"]) # test duplicate alias (should fail)

    print("\ngetting commands...")
    cmd_obj_hello = manager.get_command("hello")
    cmd_obj_hi = manager.get_command("hi")
    cmd_obj_test = manager.get_command("test")
    cmd_obj_none = manager.get_command("nonexistent")

    print(f"found 'hello': {cmd_obj_hello}")
    print(f"found 'hi': {cmd_obj_hi}")
    print(f"found 'test': {cmd_obj_test}")
    print(f"found 'nonexistent': {cmd_obj_none}")
    print(f"are 'hello' and 'hi' the same object? {cmd_obj_hello is cmd_obj_hi}")

    print("\nexecuting commands...")
    dummy_user = {"name": "tester", "tags": None}
    if cmd_obj_hello: cmd_obj_hello.execute(dummy_user, ["arg1", "arg2"])
    if cmd_obj_hi: cmd_obj_hi.execute(dummy_user, []) # execute via alias
    if cmd_obj_test: cmd_obj_test.execute(dummy_user, ["test_arg"])

    print("\ndisabling 'hello'...")
    manager.disable_command("hello")
    print(f"'hello' enabled: {cmd_obj_hello.enabled if cmd_obj_hello else 'n/a'}")
    print("executing 'hello' (should not run)...")
    if cmd_obj_hello: cmd_obj_hello.execute(dummy_user, ["arg3"])
    print("executing 'hi' (should not run)...")
    if cmd_obj_hi: cmd_obj_hi.execute(dummy_user, [])

    print("\nenabling 'hello'...")
    manager.enable_command("hello")
    print(f"'hello' enabled: {cmd_obj_hello.enabled if cmd_obj_hello else 'n/a'}")
    print("executing 'hi' (should run now)...")
    if cmd_obj_hi: cmd_obj_hi.execute(dummy_user, [])

    print("\nunregistering 'hello'...")
    manager.unregister_command("hello")
    cmd_obj_hello_after = manager.get_command("hello")
    cmd_obj_hi_after = manager.get_command("hi")
    print(f"found 'hello' after unregister: {cmd_obj_hello_after}")
    print(f"found 'hi' after unregister: {cmd_obj_hi_after}") # alias should also be gone

    print("\nall commands:")
    for cmd in manager.get_all_commands():
        print(f"- {cmd}")