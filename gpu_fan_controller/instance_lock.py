import socket

DEFAULT_LOCK_PORT = 47654


class InstanceLock:
    """用本地监听端口实现单实例锁，避免桌面程序与 systemd/网页实例同时控制风扇。

    使用 socket 而不是文件锁，是为了在 Linux 和 Windows 上行为一致，且进程异常
    退出时由内核自动释放端口。
    """

    def __init__(self, port: int = DEFAULT_LOCK_PORT, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self._sock: socket.socket | None = None

    def acquire(self) -> bool:
        if self._sock is not None:
            return True
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((self.host, self.port))
            sock.listen(1)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        return True

    def release(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @property
    def acquired(self) -> bool:
        return self._sock is not None

    def __enter__(self) -> "InstanceLock":
        if not self.acquire():
            raise RuntimeError("另一个 GPU 风扇控制器实例正在运行")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
