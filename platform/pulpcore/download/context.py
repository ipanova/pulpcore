import sys

from gettext import gettext as _
from logging import getLogger
from threading import RLock
from datetime import datetime, timedelta

log = getLogger(__name__)


class Cache:
    """
    Generic object cache.

    Attributes:
        eviction_threshold (timedelta): How long an unrequested item will be cached.
        _lock (RLock): The object mutex.
        _inventory (dict): The inventory of cached objects.
            Each value is an Item.
    """

    def __init__(self, eviction_threshold=None):
        """
        Args:
            eviction_threshold (timedelta): How long an unrequested item will be cached.

        """
        self.eviction_threshold = eviction_threshold or timedelta(hours=4)
        self._lock = RLock()
        self._inventory = {}

    def put(self, key, object_):
        """
        Put an object to the cache.

        Args:
            key (hashable): The caching key.
            object_ (object): An object to be cached.
        """
        with self._lock:
            self._inventory[key] = Item(object_)

    def purge(self, key):
        """
        Purge (delete) objects cached using the specified key.

        Args:
            key (hashable): The caching key.
        """
        with self._lock:
            return self._inventory.pop(key)

    def get(self, key):
        """
        Get a cached object by key.

        Args:
            key (hashable): The caching key.

        Returns:
            object: The requested cached object.

        Raises:
            LookupError: When not found in the cache.
        """
        with self._lock:
            try:
                item = self._inventory[key]
            except KeyError:
                raise LookupError()
            item.touch()
            self.evict()
            return item.object

    def evict(self):
        """
        Evict all unused cached objects.

        Returns:
            list: The evicted objects.
        """
        busy = []
        evicted = []
        now = Item.now()
        with self._lock:
            for key, item in self._inventory.items():
                duration = (now - item.last_requested)
                if item.busy:
                    busy.append(item.object)
                    continue
                if duration < self.eviction_threshold:
                    continue
                self.purge(key)
                evicted.append(item.object)
        log.debug(
            _('Cache.evict(): %(t)d total, %(e)d evicted, %(b)d busy'),
            {
                't': len(self._inventory),
                'e': len(evicted),
                'b': len(busy)
            })
        return evicted

    def __contains__(self, key):
        return key in self._inventory


class Item:
    """
    A cached item.
    Contained within the cache inventory and is used to
    track status and usage statistics.

    Attributes:
        last_requested (datetime): The last UTC naive time
            the object was requested.
        object (object): The actual cached object.
    """

    @staticmethod
    def now():
        """
        The current UTC naive timestamp.

        Returns:
            datetime: The current UTC naive timestamp.
        """
        return datetime.utcnow()

    def __init__(self, object_):
        """
        Args:
            object_ (object): The actual cached object.
        """
        self.last_requested = None
        self.object = object_
        self.touch()

    @property
    def ref_count(self):
        """
        The reference count.

        Returns:
            int: The reference count.

        Notes:
            Two (2) of the references are the cache and the parameter
            passed to getrefcount().  They are subtracted to reflect only
            external references.
        """
        return sys.getrefcount(self.object) - 2

    @property
    def busy(self):
        """
        The item is busy.
        An item with a ref_count > 0 is busy.

        Returns:
            bool: True if busy.
        """
        return self.ref_count > 0

    def touch(self):
        """
        Update the last_requested timestamp.
        """
        self.last_requested = self.now()


class Context:
    """
    A download context modeled after Threading.local.
    Each download has a reference to a context used to safely share resources
    such as HTTP sessions and authentication tokens.

    Attributes:
        cache (Cache): A general purpose cache.

    Examples:
        >>>
        >>> def get_token(self):
        >>>     with self.context as context:
        >>>         try:
        >>>             return context.token
        >>>         except AttributeError:
        >>>             token = self.generate_token()
        >>>             context.token = token
        >>>             return token
        >>>
    """

    def __init__(self, **properties):
        """
        Args:
            properties (dict): Initial properties.
        """
        self.__dict__.update(properties)
        self.__dict__['MUTEX'] = RLock()
        self.__dict__['cache'] = Cache()

    @property
    def _mutex(self):
        return self.__dict__['MUTEX']

    def __setattr__(self, key, value):
        with self._mutex:
            super(Context, self).__setattr__(key, value)

    def __enter__(self):
        self._mutex.acquire()
        return self

    def __exit__(self, *unused):
        self._mutex.release()
