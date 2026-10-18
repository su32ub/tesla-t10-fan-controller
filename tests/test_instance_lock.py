import socket
import unittest

from gpu_fan_controller.instance_lock import InstanceLock


class InstanceLockTests(unittest.TestCase):
    @staticmethod
    def free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_exclusive_acquire_and_release(self):
        port = self.free_port()
        first = InstanceLock(port=port)
        second = InstanceLock(port=port)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        self.assertTrue(first.acquired)
        first.release()
        self.assertTrue(second.acquire())
        second.release()
