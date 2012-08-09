from tenclouds.lock import FileLock


class RWSynchronize247(object):

    def __init__(self, template_name, reader_instance=None,
            writer_instance=None, synchronized_class=None, *args, **kwargs):
        """
        Our permanent (24/7) synchronize template can be initialized with custom
        class - synchronized_class or with instances of sync objects.
        """

        self.reader_instance = reader_instance or synchronized_class(*args,
            **kwargs)
        self.writer_instance = writer_instance or synchronized_class(*args,
            **kwargs)

        self.lock = FileLock(template_name + '_general_lock')
        self.rwlock = FileLock(template_name + '_rw_lock')

    def reader_lock(self):
        """
        Locks and returns the reader instance.
        """
        self.rwlock.acquire_shared_lock()
        return self.get_reader()

    def get_reader(self):
        """
        Returns the reader instance (without locks).
        """
        return self.reader_instance

    def reader_release(self):
        """
        Releases reader instance locks.
        """
        self.rwlock.unlock()

    def modified_lock(self):
        """
        Locks and returns the modified instance which can be modified.
        """
        self.lock.acquire_exclusive_lock()
        return self.get_modified()

    def get_modified(self):
        """
        Return modified instance (without locks).
        """
        return self.writer_instance

    def modified_release(self, func, switch=True, *args, **kwargs):
        """
        Returns modified instance's lock.

        :param switch: perform instances switch writer :=: reader
        """
        if switch:
            self._switch_with_lock(func, *args, **kwargs)
        self.lock.unlock()

    def switch(self, func, *args, **kwargs):
        """
        Cold switch. Aquires all locks and runs switch. In result whole
        template 24/7 instance is blocked for switch time.
        """
        self.lock.acquire_exclusive_lock()
        self._switch_with_lock(func, *args, **kwargs)
        self.lock.unlock()

    def _switch_with_lock(self, func, *args, **kwargs):
        """
        Hot switch. Use only when you hold the modified lock.
        """
        self.rwlock.acquire_exclusive_lock()
        func(*args, **kwargs)
        self.rwlock.unlock()
