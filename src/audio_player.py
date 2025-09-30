import logging
import queue
import threading
import time
import sounddevice as sd
import soundfile as sf
from typing import Optional, Dict, Any, List
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

# assuming eventbus is around if we need it for events like playback_started/finished
# from src.event_bus import EventBus

logger = logging.getLogger(__name__)

# define event names (optional)
EVENT_PLAYBACK_STARTED = "playback_started"
EVENT_PLAYBACK_FINISHED = "playback_finished"
EVENT_PLAYBACK_ERROR = "playback_error"

class PlaybackState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPING = "stopping"

@dataclass
class AudioItem:
    file_path: str
    title: Optional[str] = None
    duration: Optional[float] = None

class AudioPlayer:
    """handles audio playback using sounddevice and soundfile."""

    def __init__(self, config: Dict[str, Any], event_bus=None): # event_bus is optional for now
        self._config = config
        self._event_bus = event_bus
        self._playback_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._play_queue = queue.Queue() # queue to hold AudioItem objects
        self._current_stream: Optional[sd.OutputStream] = None
        self._lock = threading.RLock() # reentrant lock for nested locking
        self._target_device_id: Optional[int] = None # store the target device id
        self._state = PlaybackState.STOPPED
        self._current_item: Optional[AudioItem] = None
        self._shutdown_requested = False

        # find the target device before starting the thread
        self._target_device_id = self._find_output_device_id()

        # start the playback thread
        self._start_playback_thread()
        logger.info("AudioPlayer initialized.")

    def _find_output_device_id(self) -> Optional[int]:
        """finds the output device id based on the substring in config."""
        device_substring = self._config.get("output_device_substring")
        if not device_substring:
            logger.warning("no 'output_device_substring' found in config. using default output device.")
            return None # use default device

        logger.info(f"searching for output device containing: '{device_substring}'")
        try:
            devices = sd.query_devices()
            logger.debug(f"available devices: {devices}")
            for i, device in enumerate(devices):
                # check if it's an output device (max_output_channels > 0) and name matches substring
                if device['max_output_channels'] > 0 and device_substring.lower() in device['name'].lower():
                    logger.info(f"found matching output device: id={i}, name='{device['name']}'")
                    return i
            logger.error(f"could not find an output device matching substring: '{device_substring}'. using default device.")
            return None # fallback to default if not found
        except Exception as e:
            logger.error(f"error querying audio devices: {e}. using default device.", exc_info=True)
            return None


    def _start_playback_thread(self):
        """starts the background thread that processes the play queue."""
        if self._playback_thread and self._playback_thread.is_alive():
            logger.warning("playback thread already running.")
            return
        self._stop_event.clear()
        self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self._playback_thread.start()
        logger.info("audio playback thread started.")

    def _playback_loop(self):
        """the main loop for the playback thread."""
        logger.info("playback loop starting.") # log thread start
        while True: # loop forever until shutdown signal (none)
            # --- make sure stop event is clear at the start ---
            self._stop_event.clear()
            logger.debug("playback loop iteration started, stop event cleared.")

            # --- check for shutdown signal first ---
            # check queue for none first, 'cause stop_event might be set for skip/stop
            # file_path = self._play_queue.get(block=True) # wait forever for an item or none

            try:
                # wait for a file path in the queue (with timeout so we can check stop_event)
                file_path = self._play_queue.get(timeout=0.5)
            except queue.Empty:
                # no item in queue, just keep waiting.
                # don't check _stop_event here, skip/stop shouldn't kill the thread.
                continue # no file to play, loop again

            if file_path is None: # sentinel value for shutdown
                logger.info("shutdown sentinel (none) received in queue.")
                break # exit the main while loop

            # --- check stop event again after getting an item ---
            # this handles if stop/skip was called *while* waiting for get()
            if self._stop_event.is_set():
                 logger.info(f"stop/skip event detected immediately after getting {file_path} from queue. skipping playback.")
                 self._play_queue.task_done() # mark item as done even though skipped
                 # event gets cleared at the start of the next loop
                 continue # go to next loop iteration


            logger.info(f"attempting to start playback for: {file_path}") # changed log message slightly
            # --- load audio data ---
            # We actually don't need to preload data if using the callback with sf.SoundFile
            # try:
            #     data, samplerate = sf.read(file_path, dtype='float32')
            #     logger.debug(f"loaded audio file: {file_path}, samplerate: {samplerate}, shape: {data.shape}")
            # except sf.SoundFileError as e:
            #     logger.error(f"soundfileerror loading {file_path}: {e}")
            #     if self._event_bus: self._event_bus.publish(EVENT_PLAYBACK_ERROR, file_path=file_path, error=str(e))
            #     self._play_queue.task_done() # mark as done even on load failure
            #     continue # skip to next item in queue
            # except Exception as e:
            #     logger.error(f"unexpected error loading audio file {file_path}: {e}", exc_info=True)
            #     if self._event_bus: self._event_bus.publish(EVENT_PLAYBACK_ERROR, file_path=file_path, error=str(e))
            #     self._play_queue.task_done()
            #     continue

            # --- play audio using outputstream and callback ---
            stream = None # define stream variable outside try
            try:
                # use soundfile to open the file for reading in the callback
                with sf.SoundFile(file_path, 'r') as audio_file:
                    samplerate = audio_file.samplerate
                    channels = audio_file.channels
                    logger.debug(f"opened audio file: {file_path}, samplerate: {samplerate}, channels: {channels}")

                    # event to signal when callback is done or errored
                    stream_finished_event = threading.Event()
                    # buffer size (frames per callback) - larger buffer reduces CPU overhead
                    blocksize = 2048 # optimized for better performance

                    def callback(outdata: memoryview, frames: int, time_info, status: sd.CallbackFlags):
                        """callback function to feed audio data to the stream."""
                        if status:
                            logger.warning(f"playback status flags: {status}")
                            # You might want to signal an error or stop based on the status
                            # For example: if status.output_underflow: stream_finished_event.set()

                        try:
                            # read requested number of frames from the file
                            read_data = audio_file.read(frames, dtype='float32', always_2d=True)
                            frames_read = read_data.shape[0]

                            if frames_read == 0: # end of file reached immediately
                                logger.debug("callback: end of file reached (0 frames read).")
                                outdata[:] = 0 # fill buffer with silence
                                raise sd.CallbackStop # signal stream to stop
                            else:
                                # copy the read data into the output buffer slice
                                outdata[:frames_read] = read_data

                                if frames_read < frames: # end of file reached in this read
                                    logger.debug(f"callback: padding end of stream ({frames_read}/{frames} frames).")
                                    # zero out the remaining part of the buffer
                                    outdata[frames_read:] = 0
                                    raise sd.CallbackStop # signal stream to stop after this buffer

                        except Exception as e:
                            logger.error(f"error within audio callback for {file_path}: {e}", exc_info=True)
                            # Signal completion/error to the main thread
                            stream_finished_event.set()
                            raise sd.CallbackAbort # Abort stream on unexpected error in callback


                    def finished_callback():
                        """called when the stream finishes normally or is stopped/aborted."""
                        logger.debug(f"stream finished_callback invoked for {file_path}")
                        stream_finished_event.set() # signal completion/stop

                    logger.debug(f"attempting to create outputstream for device id: {self._target_device_id}")
                    # create and start the outputstream, specifying the device
                    stream = sd.OutputStream(
                        device=self._target_device_id, # use the found device id (or none for default)
                        samplerate=samplerate,
                        channels=channels,
                        blocksize=blocksize, # use specified blocksize
                        callback=callback,
                        finished_callback=finished_callback
                    )
                    logger.debug(f"outputstream created. attempting to start stream...")
                    with self._lock: # protect stream variable during start/stop
                         self._current_stream = stream # store ref for stop_playback
                         stream.start() # this call might block/hang
                         logger.debug(f"outputstream started successfully for {file_path}")

                    # wait for the stream to finish (signaled by finished_callback or stop_event)
                    playback_interrupted = False # flag to track if stopped early
                    while not stream_finished_event.wait(timeout=0.1):
                         if self._stop_event.is_set():
                              logger.info(f"stop requested during playback of {file_path}. stopping stream and breaking wait loop.")
                              playback_interrupted = True
                              # --- try stopping the stream directly here ---
                              try:
                                  # use the lock for safe access to stream object
                                  with self._lock:
                                      # check if it's still the current stream and not already stopped
                                      if self._current_stream == stream and stream and not stream.stopped:
                                          logger.debug("attempting to stop stream directly from wait loop...")
                                          stream.stop() # stop it now
                                          logger.debug("stream stopped directly from wait loop.")
                              except Exception as e_stop:
                                  logger.error(f"error stopping stream directly within wait loop: {e_stop}", exc_info=True)
                              # --- end direct stop attempt ---
                              break # break the wait loop

                    # --- cleanup after playback/stop ---
                    # make sure stream is stopped and closed if it exists
                    with self._lock:
                        if self._current_stream == stream and stream: # check if it's still the same stream
                            if not stream.stopped:
                                try: stream.stop()
                                except sd.PortAudioError as pae: logger.warning(f"Ignoring PortAudioError on stop: {pae}")
                            if not stream.closed:
                                try: stream.close()
                                except sd.PortAudioError as pae: logger.warning(f"Ignoring PortAudioError on close: {pae}")
                            self._current_stream = None # clear reference
                            logger.debug(f"stream stopped and closed for {file_path}")

                    # log normal finish only if stop wasn't requested and stream finished naturally
                    if not playback_interrupted and stream_finished_event.is_set():
                        logger.info(f"finished playback for: {file_path}")
                        if self._event_bus: self._event_bus.publish(EVENT_PLAYBACK_FINISHED, file_path=file_path)
                    elif playback_interrupted:
                        logger.info(f"playback interrupted for: {file_path}")
                        # don't clear the event here anymore. it's cleared at the start of the loop.
                        # self._stop_event.clear()
                        # logger.debug("stop event cleared after handling interruption.")


            except sf.SoundFileError as e:
                 logger.error(f"soundfileerror opening/reading {file_path}: {e}")
                 if self._event_bus: self._event_bus.publish(EVENT_PLAYBACK_ERROR, file_path=file_path, error=str(e))
            except sd.PortAudioError as e:
                logger.error(f"portaudioerror during playback setup for {file_path}: {e}", exc_info=True) # added exc_info
                if self._event_bus: self._event_bus.publish(EVENT_PLAYBACK_ERROR, file_path=file_path, error=str(e))
            except Exception as e:
                logger.error(f"unexpected error during playback processing of {file_path}: {e}", exc_info=True) # changed log message
                if self._event_bus: self._event_bus.publish(EVENT_PLAYBACK_ERROR, file_path=file_path, error=str(e))
            finally:
                # ensure stream is cleaned up even if errors occurred before the main wait loop
                with self._lock:
                    if self._current_stream == stream and stream and not stream.closed:
                        try:
                            if not stream.stopped: stream.stop()
                            stream.close()
                            self._current_stream = None
                            logger.debug(f"stream cleaned up in finally block for {file_path}")
                        except sd.PortAudioError as pae:
                            logger.warning(f"Ignoring PortAudioError during finally cleanup: {pae}")
                        except Exception as final_e:
                             logger.error(f"Error during final stream cleanup for {file_path}: {final_e}")

                # mark task as done regardless of success/failure
                logger.debug(f"marking task done for {file_path} in finally block.")
                self._play_queue.task_done()


        logger.warning("audio playback thread loop exited.") # changed level to warning


    def play_file(self, file_path: str, title: Optional[str] = None):
        """adds a file to the playback queue."""
        if self._shutdown_requested:
            logger.warning("Cannot queue file, AudioPlayer is shutting down")
            return
            
        if not isinstance(file_path, str) or not file_path:
             logger.error("invalid file path provided for playback.")
             return
             
        audio_item = AudioItem(file_path=file_path, title=title)
        logger.info(f"queueing file for playback: {file_path}")
        self._play_queue.put(audio_item)

    def stop_playback(self, clear_queue: bool = False): # default clear_queue to false
        """signals the playback thread to stop the current track. optionally clears the queue."""
        logger.info(f"stop playback requested. clear queue: {clear_queue}")

        # --- signal the playback thread ---
        # set the event. the playback loop's wait() will detect this.
        # the loop itself is responsible for stopping/closing the stream.
        self._stop_event.set()
        logger.debug("stop event set.")
        # --- end signal ---

        # clear the queue if requested (can still do this right away)
        if clear_queue:
            logger.debug("clearing playback queue...")
            while not self._play_queue.empty():
                try:
                    self._play_queue.get_nowait()
                    self._play_queue.task_done()
                except queue.Empty:
                    break
                except Exception as e:
                     logger.error(f"error clearing item from queue: {e}")
            logger.info("playback queue cleared.")

        # don't clear the stop event here. the playback loop handles it.
        # self._stop_event.clear() # ensure this line is removed or commented out
        # logger.debug("stop event cleared, playback loop can continue.") # ensure this is removed or commented out

    def get_queue_snapshot(self) -> List[AudioItem]:
        """returns a copy of the current items in the playback queue."""
        with self._play_queue.mutex: # access underlying queue safely
            return list(self._play_queue.queue)
            
    def get_current_state(self) -> PlaybackState:
        """returns the current playback state."""
        with self._lock:
            return self._state
            
    def get_current_item(self) -> Optional[AudioItem]:
        """returns the currently playing item."""
        with self._lock:
            return self._current_item

    def get_output_device_id(self) -> Optional[int]:
        """returns the configured output device id (or none if default)."""
        return self._target_device_id


    def shutdown(self):
        """stops the playback thread and cleans up."""
        logger.info("audioplayer shutting down...")
        with self._lock:
            self._shutdown_requested = True
            
        self.stop_playback(clear_queue=True) # stop current sound and clear queue
        self._stop_event.set() # signal the playback loop thread to exit
        self._play_queue.put(None) # add sentinel value to unblock queue.get()

        if self._playback_thread and self._playback_thread.is_alive():
            logger.debug("waiting for playback thread to finish...")
            self._playback_thread.join(timeout=5) # increased timeout
            if self._playback_thread.is_alive():
                 logger.warning("playback thread did not shut down gracefully.")
        logger.info("audioplayer shut down complete.")


# example usage (can be removed or kept for testing)
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

    # create a dummy audio file for testing (requires numpy)
    try:
        import numpy as np
        samplerate = 44100
        duration = 3 # seconds
        frequency = 440 # hz (a4 note)
        t = np.linspace(0., duration, int(samplerate * duration), endpoint=False)
        amplitude = 0.5
        audio_data = amplitude * np.sin(2. * np.pi * frequency * t)
        dummy_file = "dummy_audio.wav"
        sf.write(dummy_file, audio_data, samplerate)
        print(f"created dummy audio file: {dummy_file}")
    except ImportError:
        print("numpy not installed, cannot create dummy audio file for testing.")
        dummy_file = None
    except Exception as e:
        print(f"error creating dummy audio file: {e}")
        dummy_file = None


    # Example config dictionary
    test_config = {
        "output_device_substring": "CABLE Input" # Replace with part of your desired output device name
    }
    player = AudioPlayer(config=test_config)


    if dummy_file:
        print("\nqueueing dummy file...")
        player.play_file(dummy_file)
        player.play_file(dummy_file) # queue another one

        print("waiting a bit for playback to start...")
        time.sleep(1)

        print("\nstopping current playback (clearing queue)...")
        player.stop_playback(clear_queue=True)
        time.sleep(0.5) # give time for stop to process

        print("\nqueueing file again...")
        player.play_file(dummy_file)

        print("waiting for playback to finish naturally...")
        # wait until queue is processed
        player._play_queue.join()
        print("queue processed.")

    else:
         print("\nskipping playback test as dummy file could not be created.")


    print("\nshutting down audioplayer...")
    player.shutdown()

    # clean up dummy file
    if dummy_file and os.path.exists(dummy_file):
        try:
            os.remove(dummy_file)
            print(f"removed dummy audio file: {dummy_file}")
        except OSError as e:
            print(f"error removing dummy audio file: {e}")

    print("\naudioplayer test finished.")
