import logging
import threading
import asyncio
from collections import defaultdict
from typing import Callable, DefaultDict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import weakref

logger = logging.getLogger(__name__)

class EventBus:
    """optimized publish/subscribe event bus with async callback support."""

    def __init__(self, max_workers: int = 4):
        self._subscribers: DefaultDict[str, List[Callable]] = defaultdict(list)
        self._async_subscribers: DefaultDict[str, List[Callable]] = defaultdict(list)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="EventBus")
        self._lock = threading.RLock()  # reentrant lock for thread safety
        self._shutdown = False
        logger.info(f"EventBus initialized with {max_workers} worker threads.")

    def subscribe(self, event_type: str, callback: Callable, async_callback: bool = False):
        """subscribe a callback function to an event type."""
        if not callable(callback):
            logger.error(f"attempted to subscribe non-callable object to event '{event_type}'")
            return

        with self._lock:
            if self._shutdown:
                logger.warning(f"attempted to subscribe to event '{event_type}' on shutdown EventBus")
                return
                
            target_dict = self._async_subscribers if async_callback else self._subscribers
            target_dict[event_type].append(callback)
            callback_type = "async" if async_callback else "sync"
            logger.debug(f"{callback_type} callback {callback.__name__} subscribed to event '{event_type}'")

    def unsubscribe(self, event_type: str, callback: Callable):
        """unsubscribe a callback function from an event type."""
        with self._lock:
            # try removing from both sync and async subscribers
            removed = False
            for subscriber_dict, dict_name in [(self._subscribers, "sync"), (self._async_subscribers, "async")]:
                if event_type in subscriber_dict:
                    try:
                        subscriber_dict[event_type].remove(callback)
                        logger.debug(f"{dict_name} callback {callback.__name__} unsubscribed from event '{event_type}'")
                        removed = True
                        # clean up event type if no subscribers left
                        if not subscriber_dict[event_type]:
                            del subscriber_dict[event_type]
                        break
                    except ValueError:
                        continue
            
            if not removed:
                logger.warning(f"attempted to unsubscribe callback {callback.__name__} from event '{event_type}', but it was not found.")

    def publish(self, event_type: str, *args: Any, **kwargs: Any):
        """publish an event to all subscribed callbacks with optimized execution."""
        with self._lock:
            if self._shutdown:
                logger.debug(f"ignoring event '{event_type}' publish on shutdown EventBus")
                return
                
            sync_callbacks = self._subscribers.get(event_type, []).copy()
            async_callbacks = self._async_subscribers.get(event_type, []).copy()

        total_callbacks = len(sync_callbacks) + len(async_callbacks)
        if total_callbacks == 0:
            logger.debug(f"published event '{event_type}' but no subscribers found.")
            return

        logger.debug(f"publishing event '{event_type}' to {total_callbacks} subscribers ({len(sync_callbacks)} sync, {len(async_callbacks)} async).")

        # execute sync callbacks immediately in current thread
        for callback in sync_callbacks:
            self._execute_callback_safe(callback, event_type, args, kwargs)

        # execute async callbacks in thread pool
        if async_callbacks:
            futures = []
            for callback in async_callbacks:
                future = self._executor.submit(self._execute_callback_safe, callback, event_type, args, kwargs)
                futures.append(future)
            
            # optionally wait for completion or let them run in background
            # for now, let them run in background for better performance

    def _execute_callback_safe(self, callback: Callable, event_type: str, args: tuple, kwargs: dict):
        """safely execute a callback with comprehensive error handling."""
        try:
            callback(*args, **kwargs)
            logger.debug(f"executed callback {callback.__name__} for event '{event_type}'")
        except Exception as e:
            logger.error(f"error executing callback {callback.__name__} for event '{event_type}': {e}", exc_info=True)
            # callback errors don't propagate to prevent one bad callback from breaking others

    def shutdown(self, wait: bool = True):
        """shutdown the event bus and cleanup resources."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            
        logger.info("EventBus shutting down...")
        self._executor.shutdown(wait=wait)
        logger.info("EventBus shutdown complete.")

    def get_subscriber_count(self, event_type: str) -> int:
        """get the total number of subscribers for an event type."""
        with self._lock:
            return len(self._subscribers.get(event_type, [])) + len(self._async_subscribers.get(event_type, []))

# example usage (can be removed or kept for testing)
if __name__ == '__main__':
    # set up basic logging for testing this module directly
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')

    bus = EventBus()

    def handler1(message):
        print(f"handler 1 received: {message}")

    def handler2(message, sender=None):
        print(f"handler 2 received: {message} from {sender}")
        # test unsubscribing from within a handler
        print("handler 2 unsubscribing handler 1")
        bus.unsubscribe("test_event", handler1)

    def handler3(message):
        print(f"handler 3 received: {message}")
        raise ValueError("handler 3 failed!")


    print("\nsubscribing handlers...")
    bus.subscribe("test_event", handler1)
    bus.subscribe("test_event", handler2)
    bus.subscribe("test_event", handler3)
    bus.subscribe("other_event", handler1)

    print("\npublishing 'test_event'...")
    bus.publish("test_event", "hello world!", sender="main")

    print("\npublishing 'test_event' again (handler 1 should be gone)...")
    bus.publish("test_event", "hello again!")

    print("\npublishing 'other_event'...")
    bus.publish("other_event", "another message")

    print("\npublishing 'no_subscriber_event'...")
    bus.publish("no_subscriber_event", "this won't be seen")

    print("\ntesting unsubscribe non-existent handler...")
    bus.unsubscribe("test_event", handler1) # already removed
    bus.unsubscribe("fake_event", handler2) # non-existent event