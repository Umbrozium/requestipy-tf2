import logging
import os
import re
import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from typing import Dict, Callable, List, Optional, Pattern
from collections import deque
from dataclasses import dataclass
from enum import Enum

# assuming eventbus is in the same directory or accessible via sys.path
from src.event_bus import EventBus

logger = logging.getLogger(__name__)

class MessageType(Enum):
    CHAT_FULL = "chat_full"
    CHAT_SIMPLE = "chat_simple"
    KILL = "kill"
    CONNECT = "connect"
    SUICIDE = "suicide"
    UNDEFINED = "undefined"

@dataclass
class CompiledPattern:
    pattern: Pattern[str]
    message_type: MessageType
    
# Pre-compiled regex patterns for better performance
# Added optional timestamp prefix to the beginning of the expressions
timestamp_prefix = r"(?:\[?\d{2}/\d{2}/\d{4} - \d{2}:\d{2}:\d{2}\]?:\s)?"

COMPILED_PATTERNS = [
    CompiledPattern(
        # Change f"..." to rf"..."
        re.compile(rf"^{timestamp_prefix}(?:\*?(?:DEAD|TEAM|SPEC)\*? )?(?P<user>.+?)<(?P<steamid>U:\d+:\d+)>(?:<(?P<team>Red|Blue|Spectator|Console)>)? : (?P<message>.+)$"),
        MessageType.CHAT_FULL
    ),
    CompiledPattern(
        re.compile(rf"^{timestamp_prefix}(?P<user>[^:]+?) : (?P<message>.+)$"),
        MessageType.CHAT_SIMPLE
    ),
    CompiledPattern(
        re.compile(rf"^{timestamp_prefix}(?P<killer>.+?) killed (?P<victim>.+?) with (?P<weapon>.+?)\.(?: \(crit\))?$"),
        MessageType.KILL
    ),
    CompiledPattern(
        re.compile(rf"^{timestamp_prefix}(?P<user>.+?) connected$"),
        MessageType.CONNECT
    ),
    CompiledPattern(
        re.compile(rf"^{timestamp_prefix}(?P<user>.+?) suicided\.$"),
        MessageType.SUICIDE
    )
]

# define event names (constants)
EVENT_CHAT_RECEIVED = "chat_received"
EVENT_COMMAND_DETECTED = "command_detected"
EVENT_PLAYER_KILL = "player_kill"
EVENT_PLAYER_CONNECT = "player_connect"
EVENT_PLAYER_SUICIDE = "player_suicide"
EVENT_UNDEFINED_MESSAGE = "undefined_message"

class LogFileEventHandler(FileSystemEventHandler):
    """handles file system events for the console.log file."""

    def __init__(self, file_path: str, process_new_line: Callable[[str], None]):
        self._file_path = file_path
        self._process_new_line = process_new_line
        self._last_size = 0
        self._file = None
        self._open_file()
        logger.info(f"LogFileEventHandler initialized for: {self._file_path}")

    def _open_file(self):
        """opens the log file and seeks to the end."""
        try:
            # ensure directory exists (though it should if tf2 is running)
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
            # open file if it exists, create if not (tf2 might create it)
            # FIX: Added errors='replace' to safely handle invalid UTF-8 bytes
            self._file = open(self._file_path, 'a+', encoding='utf-8', errors='replace') 
            self._file.seek(0, os.SEEK_END) # go to the end
            self._last_size = self._file.tell()
            logger.info(f"opened log file {self._file_path} and seeked to end (position {self._last_size}).")
        except IOError as e:
            logger.error(f"error opening log file {self._file_path}: {e}")
            self._file = None # ensure file is none if open fails

    def _read_new_lines(self):
        """reads new lines added to the file since the last check."""
        if not self._file or self._file.closed:
            logger.warning("log file is not open. attempting to reopen...")
            self._open_file()
            if not self._file: # still couldn't open
                return # skip reading attempt

        try:
            current_size = os.path.getsize(self._file_path)
            if current_size < self._last_size:
                # file was likely truncated or replaced (e.g., tf2 restart)
                logger.warning(f"log file {self._file_path} size decreased. assuming truncation/replacement. resetting position.")
                self._file.seek(0, os.SEEK_END) # seek to new end
            elif current_size > self._last_size:
                # read the new content
                self._file.seek(self._last_size)
                # FIX: Read safely to EOF without byte-math
                new_content = self._file.read() 
                logger.debug(f"read {len(new_content)} characters from log file.")
                if new_content:
                    # log the raw content read before splitting lines
                    logger.debug(f"raw content read:\n---\n{new_content}\n---")
                    lines = new_content.splitlines()
                    # Process lines immediately without artificial delay
                    for i, line in enumerate(lines):
                         if line: # avoid processing empty lines
                            logger.debug(f"processing line {i+1}/{len(lines)}: '{line}'") # log each line being processed
                            self._process_new_line(line)

            self._last_size = self._file.tell() # update position after reading/seeking

        except FileNotFoundError:
             logger.error(f"log file {self._file_path} not found during read attempt. it might have been deleted.")
             if self._file and not self._file.closed:
                 self._file.close()
             self._file = None # mark as closed
             # optionally try to reopen immediately or wait for on_created
        except IOError as e:
            logger.error(f"ioerror reading log file {self._file_path}: {e}")
        except Exception as e:
            logger.error(f"unexpected error reading log file {self._file_path}: {e}", exc_info=True)


    def on_modified(self, event):
        """called when a file or directory is modified."""
        if event.src_path == self._file_path:
            # logger.debug(f"modification detected for {self._file_path}")
            self._read_new_lines()

    def on_created(self, event):
        """called when a file or directory is created."""
        if event.src_path == self._file_path:
            logger.info(f"log file {self._file_path} created. opening and seeking to end.")
            if self._file and not self._file.closed:
                self._file.close() # close previous handle if any
            self._open_file() # open the new file

    def close(self):
        """closes the file handle."""
        if self._file and not self._file.closed:
            logger.info(f"closing log file handle for {self._file_path}")
            self._file.close()
            self._file = None


# define cache size constant here or make it configurable
RECENT_LINE_CACHE_SIZE = 10

class LogReader:
    """monitors and parses the tf2 console log file."""

    def _clean_string(self, text: str) -> str:
        """Removes zero-width characters and unprintable control codes."""
        # Matches common invisible/formatting characters injected by TF2
        return re.sub(r'[\x00-\x1F\x7F-\x9F\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF]', '', text).strip()

    def __init__(self, config: Dict, event_bus: EventBus):
        self._config = config
        self._event_bus = event_bus
        self._observer = None
        self._event_handler = None
        self._monitor_thread = None
        self._stop_event = threading.Event()
        # cache for recent line hashes to prevent duplicates
        self._recent_lines_cache: deque[str] = deque(maxlen=RECENT_LINE_CACHE_SIZE)

        self._log_path = self._get_log_path()
        if not self._log_path:
            logger.error("logreader initialization failed: could not determine log path.")
            # consider raising an exception or setting an error state

    def _get_log_path(self) -> str | None:
        """constructs the full path to the console.log file."""
        game_dir = self._config.get("game_dir")
        log_file = self._config.get("log_file_name", "console.log") # default to console.log
        if not game_dir:
            logger.error("tf2 'game_dir' not specified in configuration.")
            return None
        if not os.path.isdir(game_dir):
             logger.error(f"configured 'game_dir' does not exist or is not a directory: {game_dir}")
             return None
        return os.path.join(game_dir, log_file)

    def _process_line(self, line: str):
        """parses a single line from the log and publishes events with optimized pattern matching."""
        line = line.strip()
        if not line:
            return
            
        logger.debug(f"processing line: {line}")

        # Try each compiled pattern in order of likelihood
        for compiled_pattern in COMPILED_PATTERNS:
            match = compiled_pattern.pattern.match(line)
            if match:
                self._handle_pattern_match(match, compiled_pattern.message_type, line)
                return
                
        # No pattern matched - publish undefined message
        logger.debug(f"undefined message: {line}")
        self._event_bus.publish(EVENT_UNDEFINED_MESSAGE, message=line)
        
    def _handle_pattern_match(self, match: re.Match, message_type: MessageType, line: str):
        """handle a successful pattern match based on message type."""
        if message_type in (MessageType.CHAT_FULL, MessageType.CHAT_SIMPLE):
            self._handle_chat_match(match, message_type)
        elif message_type == MessageType.KILL:
            self._handle_kill_match(match)
        elif message_type == MessageType.CONNECT:
            self._handle_connect_match(match)
        elif message_type == MessageType.SUICIDE:
            self._handle_suicide_match(match)
            
    def _handle_chat_match(self, match: re.Match, message_type: MessageType):
        """handle chat message matches with optimized user processing."""
        raw_user_name = (match.group('user') or "")
        message = (match.group('message') or "")
        
        # FIX: Clean the strings of invisible characters first
        raw_user_name = self._clean_string(raw_user_name)
        message = self._clean_string(message)
        
        if not raw_user_name or not message:
            return
            
        # Extract additional data based on message type
        steamid = match.group('steamid') if message_type == MessageType.CHAT_FULL else None
        # ... rest of the code remains the same ...
        team = match.group('team') if message_type == MessageType.CHAT_FULL else None

        # Process username and tags
        user_name, stripped_tags = self._process_username_tags(raw_user_name)
        if not user_name:
            logger.warning(f"Could not extract final user name after stripping tags from: {raw_user_name}")
            return
            
        # Create user info
        final_tags = " ".join(stripped_tags) if stripped_tags else None
        user_info = {"name": user_name, "steamid": steamid, "tags": final_tags, "team": team}

        # Process chat message
        if message.startswith("!") and len(message) > 1:
            # Command detected
            parts = message.split(maxsplit=1)
            command = parts[0][1:] # <--- FIX: Slice off the first character '!'
            args_str = parts[1].strip() if len(parts) > 1 else ""
            args_list = args_str.split()
            logger.info(f"Command detected: user={user_info['name']}, command={command}, args={args_list}")
            self._event_bus.publish(EVENT_COMMAND_DETECTED, user=user_info, command=command, args=args_list)
        else:
            # Regular chat message
            logger.info(f"Chat received: user={user_info['name']}, message='{message}'")
            self._event_bus.publish(EVENT_CHAT_RECEIVED, user=user_info, message=message)
            
    def _handle_kill_match(self, match: re.Match):
        """handle kill event matches."""
        kill_data = match.groupdict()
        logger.info(f"kill detected: {kill_data['killer']} killed {kill_data['victim']} with {kill_data['weapon']}")
        self._event_bus.publish(EVENT_PLAYER_KILL, killer=kill_data['killer'], victim=kill_data['victim'], weapon=kill_data['weapon'])
        
    def _handle_connect_match(self, match: re.Match):
        """handle player connect matches."""
        user = match.group('user')
        logger.info(f"player connected: {user}")
        self._event_bus.publish(EVENT_PLAYER_CONNECT, user=user)
        
    def _handle_suicide_match(self, match: re.Match):
        """handle player suicide matches."""
        user = match.group('user')
        logger.info(f"player suicide: {user}")
        self._event_bus.publish(EVENT_PLAYER_SUICIDE, user=user)
        
    def _process_username_tags(self, raw_user_name: str) -> tuple[str, List[str]]:
        """efficiently process username tags with optimized algorithm."""
        user_name = raw_user_name
        stripped_tags = []
        # FIX: Added parentheses variations of the tags
        possible_tags = ["*DEAD*", "*TEAM*", "[TEAM]", "(TEAM)", "*SPEC*", "[SPEC]", "(SPEC)", "[DEAD]", "(DEAD)"]
        
        # Pre-sort tags by length (longest first) for better matching
        possible_tags.sort(key=len, reverse=True)
        
        max_iterations = 10  # prevent infinite loops
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            tag_found = False
            original_name = user_name
            
            for tag in possible_tags:
                if user_name.startswith(tag + " "):
                    stripped_tags.append(tag)
                    user_name = user_name[len(tag)+1:].strip()
                    tag_found = True
                    break
                elif user_name.startswith(tag):
                    stripped_tags.append(tag)
                    user_name = user_name[len(tag):].strip()
                    tag_found = True
                    break
                    
            if not tag_found or user_name == original_name:
                break
                
        return user_name, stripped_tags


    def start_monitoring(self) -> threading.Thread | None:
        """
        Starts monitoring the log file in a separate thread.

        Returns:
            The monitoring thread object if started successfully, None otherwise.
        """
        if not self._log_path:
            logger.error("cannot start monitoring: log path is not configured correctly.")
            return None

        if self._observer and self._observer.is_alive():
            logger.warning("monitoring is already active.")
            return self._monitor_thread # Return existing thread if already running

        self._stop_event.clear()
        self._event_handler = LogFileEventHandler(self._log_path, self._process_line)
        self._observer = Observer()
        # watch the directory containing the file, as file modifications might
        # be detected more reliably this way, especially across different os/editors.
        watch_dir = os.path.dirname(self._log_path)
        self._observer.schedule(self._event_handler, watch_dir, recursive=False)

        # start observer in a background thread
        self._monitor_thread = threading.Thread(target=self._run_observer, daemon=True, name="LogReaderMonitorThread") # Give thread a name
        self._monitor_thread.start()
        logger.info(f"started monitoring log file: {self._log_path}")
        return self._monitor_thread # Return the newly created thread

    def _run_observer(self):
        """internal method to run the observer loop and add polling."""
        self._observer.start()
        logger.debug("watchdog observer started.")
        polling_interval = 2 # check file every 2 seconds as a fallback

        try:
            while not self._stop_event.is_set():
                # --- polling check ---
                # periodically check for new lines, even if no event was received.
                # the _read_new_lines method handles checking size and avoids re-reading.
                try:
                    if self._event_handler: # ensure handler exists
                        self._event_handler._read_new_lines()
                except Exception as e:
                     logger.error(f"error during periodic log poll check: {e}", exc_info=True)

                # --- wait ---
                # wait for the polling interval or until stop event is set
                self._stop_event.wait(timeout=polling_interval)

        except Exception as e:
            logger.error(f"error in observer/polling control thread: {e}", exc_info=True)
        finally:
            if self._observer.is_alive():
                self._observer.stop()
            self._observer.join() # wait for observer thread to finish
            if self._event_handler:
                self._event_handler.close() # close the file handle
            logger.debug("watchdog observer stopped.")


    def stop_monitoring(self):
        """stops monitoring the log file."""
        if self._observer and self._observer.is_alive():
            logger.info("stopping log file monitoring...")
            self._stop_event.set() # signal the observer loop to exit
            # observer stopping and joining happens in _run_observer finally block
            if self._monitor_thread:
                 self._monitor_thread.join(timeout=5) # wait for thread to finish
                 if self._monitor_thread.is_alive():
                     logger.warning("monitoring thread did not stop gracefully.")
            logger.info("log file monitoring stopped.")
        else:
            logger.info("log file monitoring was not active.")

# example usage (can be removed or kept for testing)
if __name__ == '__main__':
    # set up basic logging and event bus for testing
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    test_bus = EventBus()

    # dummy handlers
    def handle_cmd(user, command, args): print(f"command: user={user['name']}, cmd={command}, args={args}")
    def handle_chat(user, message): print(f"chat: user={user['name']}, msg='{message}'")
    def handle_kill(killer, victim, weapon): print(f"kill: {killer} killed {victim} with {weapon}")
    def handle_other(message): print(f"other: {message}")

    test_bus.subscribe(EVENT_COMMAND_DETECTED, handle_cmd)
    test_bus.subscribe(EVENT_CHAT_RECEIVED, handle_chat)
    test_bus.subscribe(EVENT_PLAYER_KILL, handle_kill)
    test_bus.subscribe(EVENT_UNDEFINED_MESSAGE, handle_other)

    # create a dummy config and log file for testing
    TEST_DIR = "temp_test_tf2_log"
    TEST_LOG_FILE = os.path.join(TEST_DIR, "console.log")
    if not os.path.exists(TEST_DIR): os.makedirs(TEST_DIR)
    # clear or create the log file
    with open(TEST_LOG_FILE, "w") as f: f.write("")

    test_config = {"game_dir": TEST_DIR, "log_file_name": "console.log"}

    # initialize and start reader
    reader = LogReader(test_config, test_bus)
    reader.start_monitoring()

    print(f"monitoring {TEST_LOG_FILE}. append lines to test (e.g., using another terminal or script). press ctrl+c to stop.")
    print("examples to append:")
    print('echo "testuser : hello there!" >> temp_test_tf2_log/console.log')
    print('echo "testuser : !play some song" >> temp_test_tf2_log/console.log')
    print('echo "player1 killed player2 with scattergun." >> temp_test_tf2_log/console.log')
    print('echo "this is some other log line" >> temp_test_tf2_log/console.log')


    try:
        # keep main thread alive for testing
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopping test...")
    finally:
        reader.stop_monitoring()
        # clean up dummy file/dir
        # import shutil
        # if os.path.exists(test_dir): shutil.rmtree(test_dir)
        print("test finished.")
